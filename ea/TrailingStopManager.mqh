//+------------------------------------------------------------------+
//|                                                TrailingStopManager |
//|                    EA-native ATR trailing stop manager             |
//|                    Survives MCP/gateway instability                |
//+------------------------------------------------------------------+
#property copyright "MT5-mcp"
#property version   "1.0"

#include <Trade\Trade.mqh>
#include <Indicators\Trend.mqh>

//+------------------------------------------------------------------+
//| CTrailingStopManager — manages ATR-based trailing stops           |
//|                                                                  |
//| State survives OnTimer() ticks but not EA restarts.              |
//| For persistence across restarts, use SetTrailingStopRequest        |
//| server-side or re-register after EA reinit.                       |
//+------------------------------------------------------------------+
class CTrailingStopManager
{
private:
   // Dynamic arrays for state
   ulong             m_tickets[];
   double            m_atr_multipliers[];
   double            m_lock_in_values[];       // lock_in_profit_atr threshold
   double            m_last_sl[];              // last applied SL
   double            m_entry_price[];          // entry price at registration
   int               m_position_types[];       // POSITION_TYPE_BUY or POSITION_TYPE_SELL
   string            m_symbols[];              // symbol for each position
   datetime          m_last_check_time[];      // last check timestamp
   int               m_check_intervals[];      // check_interval_seconds
   long              m_magic_numbers[];        // magic number filter per entry
   int               m_count;

   // Global settings
   int               m_filter_magic_number;    // 0 = no filter, >0 = only trail matching magic

   // ATR cache (shared handle to avoid recreating per position)
   int               m_atr_handles[];
   string            m_atr_handle_symbols[];

   CTrade            m_trade;

   //--- Find index of ticket in array, -1 if not found
   int               FindIndex(const ulong ticket) const;

   //--- Get ATR value for symbol (cached handles)
   double            GetATR(const string symbol);

   //--- Release ATR handle at index
   void              ReleaseATRHandle(const int idx);

public:
                     CTrailingStopManager();
                    ~CTrailingStopManager();

   //--- Core API
   bool              StartTrailing(ulong ticket, double atr_multiplier, int check_interval_seconds, double lock_in_profit_atr = 0.0, long magic_filter = 0);
   bool              StopTrailing(const ulong ticket);
   int               ProcessAll();              // returns count of positions processed
   int               GetActiveCount() const;
   bool              IsActive(const ulong ticket) const;

   //--- Get active list as JSON string
   string            GetActiveList() const;

   //--- Set global magic number filter (0 = no filter)
   void              SetMagicFilter(long magic) { m_filter_magic_number = (int)magic; }
};

//+------------------------------------------------------------------+
//| Constructor                                                       |
//+------------------------------------------------------------------+
CTrailingStopManager::CTrailingStopManager()
{
   m_count = 0;
   m_filter_magic_number = 0;
   ArrayResize(m_tickets, 64);
   ArrayResize(m_atr_multipliers, 64);
   ArrayResize(m_lock_in_values, 64);
   ArrayResize(m_last_sl, 64);
   ArrayResize(m_entry_price, 64);
   ArrayResize(m_position_types, 64);
   ArrayResize(m_symbols, 64);
   ArrayResize(m_last_check_time, 64);
   ArrayResize(m_check_intervals, 64);
   ArrayResize(m_magic_numbers, 64);
   ArrayResize(m_atr_handles, 16);
   ArrayResize(m_atr_handle_symbols, 16);
   ArrayFill(m_atr_handles, 0, ArraySize(m_atr_handles), INVALID_HANDLE);
}

//+------------------------------------------------------------------+
//| Destructor — release all ATR handles                              |
//+------------------------------------------------------------------+
CTrailingStopManager::~CTrailingStopManager()
{
   for(int i = 0; i < ArraySize(m_atr_handles); i++)
   {
      if(m_atr_handles[i] != INVALID_HANDLE)
         IndicatorRelease(m_atr_handles[i]);
   }
}

//+------------------------------------------------------------------+
//| Find ticket index                                                 |
//+------------------------------------------------------------------+
int CTrailingStopManager::FindIndex(const ulong ticket) const
{
   for(int i = 0; i < m_count; i++)
   {
      if(m_tickets[i] == ticket)
         return i;
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Get cached ATR handle or create new one                           |
//+------------------------------------------------------------------+
double CTrailingStopManager::GetATR(const string symbol)
{
   // Find existing handle
   int handle_idx = -1;
   for(int i = 0; i < ArraySize(m_atr_handle_symbols); i++)
   {
      if(m_atr_handle_symbols[i] == symbol)
      {
         handle_idx = i;
         break;
      }
   }

   // Create new handle if not found
   if(handle_idx == -1)
   {
      // Find free slot or expand
      handle_idx = -1;
      for(int i = 0; i < ArraySize(m_atr_handles); i++)
      {
         if(m_atr_handles[i] == INVALID_HANDLE)
         {
            handle_idx = i;
            break;
         }
      }
      if(handle_idx == -1)
      {
         int new_size = ArraySize(m_atr_handles) + 8;
         ArrayResize(m_atr_handles, new_size);
         ArrayResize(m_atr_handle_symbols, new_size);
         handle_idx = ArraySize(m_atr_handles) - 8;
         for(int i = handle_idx; i < new_size; i++)
            m_atr_handles[i] = INVALID_HANDLE;
      }

      m_atr_handles[handle_idx] = iATR(symbol, PERIOD_H1, 14);
      m_atr_handle_symbols[handle_idx] = symbol;

      if(m_atr_handles[handle_idx] == INVALID_HANDLE)
      {
         Print("TrailingStopManager: Failed to create ATR handle for ", symbol);
         return 0.0;
      }
   }

   double atr_buf[];
   ArraySetAsSeries(atr_buf, true);
   if(CopyBuffer(m_atr_handles[handle_idx], 0, 0, 1, atr_buf) < 1)
      return 0.0;

   return atr_buf[0];
}

//+------------------------------------------------------------------+
//| Release ATR handle                                                |
//+------------------------------------------------------------------+
void CTrailingStopManager::ReleaseATRHandle(const int idx)
{
   if(idx >= 0 && idx < ArraySize(m_atr_handles) && m_atr_handles[idx] != INVALID_HANDLE)
   {
      IndicatorRelease(m_atr_handles[idx]);
      m_atr_handles[idx] = INVALID_HANDLE;
      m_atr_handle_symbols[idx] = "";
   }
}

//+------------------------------------------------------------------+
//| Start trailing for a position                                     |
//+------------------------------------------------------------------+
bool CTrailingStopManager::StartTrailing(ulong ticket, double atr_multiplier, int check_interval_seconds, double lock_in_profit_atr = 0.0, long magic_filter = 0)
{
   // Validate parameters
   if(atr_multiplier < 0.5 || atr_multiplier > 5.0)
   {
      Print("TrailingStopManager: ATR multiplier out of range (0.5-5.0): ", atr_multiplier);
      return false;
   }
   if(check_interval_seconds < 1)
      check_interval_seconds = 1;

   // Verify position exists
   if(!PositionSelectByTicket(ticket))
   {
      Print("TrailingStopManager: Position not found: ", ticket);
      return false;
   }

   // Check magic number filter
   long pos_magic = PositionGetInteger(POSITION_MAGIC);
   if(m_filter_magic_number > 0 && pos_magic != m_filter_magic_number)
   {
      Print("TrailingStopManager: Position magic ", pos_magic, " doesn't match filter ", m_filter_magic_number);
      return false;
   }
   if(magic_filter > 0 && pos_magic != magic_filter)
   {
      Print("TrailingStopManager: Position magic ", pos_magic, " doesn't match per-entry filter ", magic_filter);
      return false;
   }

   // Check if already trailing
   if(FindIndex(ticket) >= 0)
   {
      Print("TrailingStopManager: Already trailing ticket ", ticket);
      return false;
   }

   // Expand arrays if needed
   if(m_count >= ArraySize(m_tickets))
   {
      int new_size = ArraySize(m_tickets) + 32;
      ArrayResize(m_tickets, new_size);
      ArrayResize(m_atr_multipliers, new_size);
      ArrayResize(m_lock_in_values, new_size);
      ArrayResize(m_last_sl, new_size);
      ArrayResize(m_entry_price, new_size);
      ArrayResize(m_position_types, new_size);
      ArrayResize(m_symbols, new_size);
      ArrayResize(m_last_check_time, new_size);
      ArrayResize(m_check_intervals, new_size);
      ArrayResize(m_magic_numbers, new_size);
   }

   // Store position state
   int idx = m_count;
   m_tickets[idx] = ticket;
   m_atr_multipliers[idx] = atr_multiplier;
   m_lock_in_values[idx] = lock_in_profit_atr;
   m_position_types[idx] = (int)PositionGetInteger(POSITION_TYPE);
   m_symbols[idx] = PositionGetString(POSITION_SYMBOL);
   m_entry_price[idx] = PositionGetDouble(POSITION_PRICE_OPEN);
   m_last_sl[idx] = PositionGetDouble(POSITION_SL);
   m_last_check_time[idx] = TimeCurrent();
   m_check_intervals[idx] = check_interval_seconds;
   m_magic_numbers[idx] = magic_filter;
   m_count++;

   Print("TrailingStopManager: Started trailing ticket ", ticket,
         " symbol=", m_symbols[idx],
         " atr_mult=", atr_multiplier,
         " lock_in=", lock_in_profit_atr,
         " interval=", check_interval_seconds, "s",
         " entry=", m_entry_price[idx],
         " initial_sl=", m_last_sl[idx]);

   return true;
}

//+------------------------------------------------------------------+
//| Stop trailing for a position                                      |
//+------------------------------------------------------------------+
bool CTrailingStopManager::StopTrailing(const ulong ticket)
{
   int idx = FindIndex(ticket);
   if(idx < 0)
   {
      Print("TrailingStopManager: Ticket ", ticket, " not found in trailing list");
      return false;
   }

   // Remove by shifting remaining entries
   for(int i = idx; i < m_count - 1; i++)
   {
      m_tickets[i] = m_tickets[i + 1];
      m_atr_multipliers[i] = m_atr_multipliers[i + 1];
      m_lock_in_values[i] = m_lock_in_values[i + 1];
      m_last_sl[i] = m_last_sl[i + 1];
      m_entry_price[i] = m_entry_price[i + 1];
      m_position_types[i] = m_position_types[i + 1];
      m_symbols[i] = m_symbols[i + 1];
      m_last_check_time[i] = m_last_check_time[i + 1];
      m_check_intervals[i] = m_check_intervals[i + 1];
      m_magic_numbers[i] = m_magic_numbers[i + 1];
   }
   m_count--;

   Print("TrailingStopManager: Stopped trailing ticket ", ticket);
   return true;
}

//+------------------------------------------------------------------+
//| Process all trailing positions                                    |
//| Returns number of positions processed                             |
//+------------------------------------------------------------------+
int CTrailingStopManager::ProcessAll()
{
   datetime now = TimeCurrent();
   int processed = 0;
   int updated = 0;
   int errors = 0;

   for(int i = 0; i < m_count; i++)
   {
      ulong ticket = m_tickets[i];

      // Check if position still exists
      if(!PositionSelectByTicket(ticket))
      {
         Print("TrailingStopManager: Position ", ticket, " no longer exists, removing");
         StopTrailing(ticket);
         i--; // adjust index after removal
         continue;
      }

      // Check check_interval
      if(now - m_last_check_time[i] < m_check_intervals[i])
         continue;

      m_last_check_time[i] = now;
      processed++;

      string symbol = m_symbols[i];
      int pos_type = m_position_types[i];
      double atr = GetATR(symbol);

      if(atr <= 0)
      {
         errors++;
         continue;
      }

      double atr_distance = atr * m_atr_multipliers[i];
      double current_sl = PositionGetDouble(POSITION_SL);
      double last_sl_val = m_last_sl[i];
       int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double min_distance = stops_level * point;
      double new_sl = 0;

      if(pos_type == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
         new_sl = bid - atr_distance;

         // Breakeven / lock-in trigger
         if(m_lock_in_values[i] > 0)
         {
            double lock_in_price = m_entry_price[i] + (atr * m_lock_in_values[i]);
            if(bid < lock_in_price)
               continue; // not yet in profit enough to start trailing
            // Once triggered, trail from entry as minimum
            new_sl = MathMax(new_sl, m_entry_price[i]);
         }

         // Only move SL UP (never widen)
         if(current_sl > 0 && new_sl <= current_sl)
            continue; // no improvement

         // Normalize
         new_sl = NormalizeDouble(new_sl, (int)digits);

         // Check stops level
         if(min_distance > 0 && MathAbs(bid - new_sl) < min_distance)
            continue; // too close to market

         // Apply
         m_trade.PositionModify(ticket, new_sl, PositionGetDouble(POSITION_TP));
         uint retcode = m_trade.ResultRetcode();
         if(retcode == 10009 || retcode == 10025)
         {
            m_last_sl[i] = new_sl;
            updated++;
            Print("TrailingStopManager: BUY ", ticket, " SL moved to ", new_sl);
         }
         else
         {
            errors++;
            Print("TrailingStopManager: BUY ", ticket, " modify failed: ", retcode);
         }
      }
      else if(pos_type == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
         new_sl = ask + atr_distance;

         // Breakeven / lock-in trigger
         if(m_lock_in_values[i] > 0)
         {
            double lock_in_price = m_entry_price[i] - (atr * m_lock_in_values[i]);
            if(ask > lock_in_price)
               continue; // not yet in profit enough to start trailing
            // Once triggered, trail from entry as maximum
            new_sl = MathMin(new_sl, m_entry_price[i]);
         }

         // Only move SL DOWN (never widen)
         if(current_sl > 0 && new_sl >= current_sl)
            continue; // no improvement

         // Normalize
         new_sl = NormalizeDouble(new_sl, (int)digits);

         // Check stops level
         if(min_distance > 0 && MathAbs(ask - new_sl) < min_distance)
            continue; // too close to market

         // Apply
         m_trade.PositionModify(ticket, new_sl, PositionGetDouble(POSITION_TP));
         uint retcode = m_trade.ResultRetcode();
         if(retcode == 10009 || retcode == 10025)
         {
            m_last_sl[i] = new_sl;
            updated++;
            Print("TrailingStopManager: SELL ", ticket, " SL moved to ", new_sl);
         }
         else
         {
            errors++;
            Print("TrailingStopManager: SELL ", ticket, " modify failed: ", retcode);
         }
      }
   }

   if(processed > 0)
      Print("TrailingStopManager: ProcessAll() — processed=", processed, " updated=", updated, " errors=", errors);

   return processed;
}

//+------------------------------------------------------------------+
//| Get active count                                                  |
//+------------------------------------------------------------------+
int CTrailingStopManager::GetActiveCount() const
{
   return m_count;
}

//+------------------------------------------------------------------+
//| Check if ticket is being trailed                                  |
//+------------------------------------------------------------------+
bool CTrailingStopManager::IsActive(const ulong ticket) const
{
   return FindIndex(ticket) >= 0;
}

//+------------------------------------------------------------------+
//| Get active trailing stops as JSON                                 |
//+------------------------------------------------------------------+
string CTrailingStopManager::GetActiveList() const
{
   string out = "{\"active_trailing\":[";
   for(int i = 0; i < m_count; i++)
   {
      if(i > 0) out += ",";
      out += StringFormat(
         "{\"ticket\":\"%I64d\",\"symbol\":\"%s\",\"atr_multiplier\":%G,\"lock_in_atr\":%G,\"last_sl\":%G,\"entry_price\":%G,\"check_interval\":%d}",
         m_tickets[i],
         m_symbols[i],
         m_atr_multipliers[i],
         m_lock_in_values[i],
         m_last_sl[i],
         m_entry_price[i],
         m_check_intervals[i]
      );
   }
   out += "],\"count\":" + IntegerToString(m_count) + "}";
   return out;
}
