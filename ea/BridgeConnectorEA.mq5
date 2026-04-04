//+------------------------------------------------------------------+
//|                                                BridgeConnectorEA |
//|                             MT5 ↔ MCP EA bridge (heartbeat)       |
//+------------------------------------------------------------------+
#property copyright "MT5-mcp"
#property version   "2.32"

// Trading includes
#include <Trade\Trade.mqh>

// Inputs
input string GatewayBaseURL = "http://127.0.0.1:8020";
input string GatewayURL = "http://127.0.0.1:8020/bridge/terminal/heartbeat"; // deprecated, use GatewayBaseURL
input int    HeartbeatSeconds = 1;        // Heartbeat interval (min 1s for EventSetTimer)
input int    CommandPollIntervalMs = 100; // Milliseconds between command polls (Option A: faster polling)
input int    MaxCommandsPerTick = 20;     // Max commands to process per timer tick (Option B: batch processing)
input bool   EnableDebugLogs = false;

// Internal state
int g_last_status = 0;

void DebugLog(const string message)
{
   if(EnableDebugLogs)
      Print(message);
}

string JsonEscape(const string s)
{
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   return out;
}

string BuildHeartbeatJson()
{
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   string server = AccountInfoString(ACCOUNT_SERVER);
   int build = (int)TerminalInfoInteger(TERMINAL_BUILD);
   datetime now = TimeCurrent();
   string payload = StringFormat(
      "{\"server\":\"%s\",\"build\":%d,\"account_id\":\"%I64d\",\"login\":%I64d,\"timestamp\":\"%s\"}",
      JsonEscape(server), build, login, login, TimeToString(now, TIME_DATE|TIME_SECONDS)
   );
   return payload;
}

int HttpPost(const string url, const string json, string &resp_out)
{
   // Ensure JSON content-type so FastAPI parses body
   string headers = "Content-Type: application/json\r\n";
   char data[];
   StringToCharArray(json, data, 0, StringLen(json), CP_UTF8);
   char result[];
   string response_headers;
   ResetLastError();
   int code = WebRequest("POST", url, headers, 10000, data, result, response_headers);
   resp_out = CharArrayToString(result, 0, -1, CP_UTF8);
   return code;
}

int OnInit()
  {
   Print("BridgeConnectorEA initialized (heartbeat)");
   EventSetTimer(HeartbeatSeconds);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   Print("BridgeConnectorEA deinitialized: ", reason);
   EventKillTimer();
  }

void OnTimer()
{
   SendHeartbeat();
   ProcessAllPendingCommands();  // Option B: process ALL pending commands per tick
}

void SendHeartbeat()
{
   string json = BuildHeartbeatJson();
   string resp;
   int code = HttpPost(GatewayURL, json, resp);
   g_last_status = code;
   if(code != 200)
      Print("Heartbeat failed code=", code, " last_error=", GetLastError(), " response=", resp);
}

// Option B: Process ALL pending commands in one timer tick
// Instead of one-per-tick, this loops with Sleep(CommandPollIntervalMs)
// between polls to drain the entire queue.
void ProcessAllPendingCommands()
{
   int processed = 0;
   int empty_polls = 0;
   const int MAX_EMPTY_POLLS = 2;  // Stop after 2 consecutive empty polls (queue is drained)
   
   while(processed < MaxCommandsPerTick && empty_polls < MAX_EMPTY_POLLS)
   {
      string cmd = NextCommand();
      
      if(cmd == "" || cmd == "NONE")
      {
         empty_polls++;
         if(empty_polls < MAX_EMPTY_POLLS)
            Sleep(CommandPollIntervalMs);  // Option A: fast polling between checks
         continue;
      }
      
      // Got a command — process it
      empty_polls = 0;  // Reset empty counter
      ProcessCommand(cmd);
      processed++;
      
      // Small sleep between commands to avoid overwhelming the terminal
      // but much faster than the old one-per-tick model
      if(CommandPollIntervalMs > 0)
         Sleep(CommandPollIntervalMs);
   }
   
   if(processed > 0 && EnableDebugLogs)
      Print("BridgeConnectorEA: Processed ", processed, " commands in this tick");
}

string HttpGet(const string url)
{
   char result[];
   char data[];
   string headers;
   string response_headers;
   ResetLastError();
   // No request body for GET
   int code = WebRequest("GET", url, headers, 5000, data, result, response_headers);
   if(code==200)
      return CharArrayToString(result, 0, -1, CP_UTF8);
   return "";
}

string NextCommand()
{
   string url = GatewayBaseURL + "/bridge/commands/next";
   string resp = HttpGet(url);
   if(resp!="" && resp!="NONE")
      DebugLog(StringFormat("DEBUG_NEXTCMD: url=[%s] response=[%s]", url, resp));
   return resp;
}

bool ParseKV(const string s, const string key, string &value)
{
   string parts[];
   int n = StringSplit(s, '&', parts);
   for(int i=0;i<n;i++){
      string kv[]; int m = StringSplit(parts[i], '=', kv);
      if(m==2 && kv[0]==key){
         value = kv[1];
         return true;
      }
   }
   return false;
}

bool EnsureSymbolInMarketWatch(const string symbol)
{
   bool ok = SymbolSelect(symbol, true);
   if(!ok)
      Print("BridgeConnectorEA: Failed to add symbol ", symbol, " to Market Watch, last_error=", GetLastError());
   return ok;
}

string JsonBars(const string symbol, const string timeframe, const int count)
{
   EnsureSymbolInMarketWatch(symbol);
   
   ENUM_TIMEFRAMES tf = TfFromString(timeframe);
   if(tf == PERIOD_CURRENT) tf = PERIOD_M1;  // Default to M1 if invalid
   
   MqlRates rates[];
   ArrayResize(rates, count);
   int copied = CopyRates(symbol, tf, 0, count, rates);
   
   if(copied <= 0)
   {
      Print("BridgeConnectorEA: No bars for ", symbol, " ", timeframe, ", copied=", copied, ", last_error=", GetLastError());
      return StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"data\":[],\"error\":\"no_data\",\"last_error\":%d}", symbol, timeframe, GetLastError());
   }
   
   string out = StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"count\":%d,\"data\":[", symbol, timeframe, copied);
   for(int i= copied-1; i>=0; i--){
      string item = StringFormat("{\"time\":%I64d,\"open\":%G,\"high\":%G,\"low\":%G,\"close\":%G,\"tick_volume\":%I64d}",
         rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, rates[i].tick_volume);
      out += item; if(i>0) out += ",";
   }
   out += "]}";
   return out;
}

string JsonIndicator(const string symbol, const string timeframe, const string indicator, const int period)
{
   EnsureSymbolInMarketWatch(symbol);
   
   ENUM_TIMEFRAMES tf = PERIOD_M1;
   if(timeframe=="M5") tf=PERIOD_M5; else if(timeframe=="M15") tf=PERIOD_M15; else if(timeframe=="M30") tf=PERIOD_M30;
   else if(timeframe=="H1") tf=PERIOD_H1; else if(timeframe=="H4") tf=PERIOD_H4; else if(timeframe=="D1") tf=PERIOD_D1;
   
   double buff[];
   string name = indicator;
   StringToLower(name);
   if(name=="sma"){
      int handle = iMA(symbol, tf, period, 0, MODE_SMA, PRICE_CLOSE);
      if(handle==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff,true);
      int copied = CopyBuffer(handle,0,0,period,buff);
      if(copied <= 0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", copied);
      double value = buff[0];
      return StringFormat("{\"indicator\":\"sma\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", period, value, symbol);
   } else if(name=="ema"){
      int handle = iMA(symbol, tf, period, 0, MODE_EMA, PRICE_CLOSE);
      if(handle==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff,true);
      int copied = CopyBuffer(handle,0,0,period,buff);
      if(copied <= 0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", copied);
      double value = buff[0];
      return StringFormat("{\"indicator\":\"ema\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", period, value, symbol);
   } else if(name=="wma"){
      int handle = iMA(symbol, tf, period, 0, MODE_LWMA, PRICE_CLOSE);
      if(handle==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff,true);
      int copied = CopyBuffer(handle,0,0,period,buff);
      if(copied <= 0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", copied);
      double value = buff[0];
      return StringFormat("{\"indicator\":\"wma\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", period, value, symbol);
   } else if(name=="smma"){
      int handle = iMA(symbol, tf, period, 0, MODE_SMMA, PRICE_CLOSE);
      if(handle==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff,true);
      int copied = CopyBuffer(handle,0,0,period,buff);
      if(copied <= 0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", copied);
      double value = buff[0];
      return StringFormat("{\"indicator\":\"smma\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", period, value, symbol);
   } else if(name=="rsi"){
      int handle = iRSI(symbol, tf, period, PRICE_CLOSE);
      if(handle==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff,true);
      int copied = CopyBuffer(handle,0,0,period,buff);
      if(copied <= 0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", copied);
      double value = buff[0];
      return StringFormat("{\"indicator\":\"rsi\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", period, value, symbol);
   }
   return "{\"error\":\"unknown_indicator\"}";
}

string JsonIndicatorAdvanced(const string symbol, const string timeframe, const string name, const string kv)
{
   EnsureSymbolInMarketWatch(symbol);
   
   ENUM_TIMEFRAMES tf = TfFromString(timeframe);
   double buff1[]; double buff2[]; double buff3[];
   string nm = name; StringToLower(nm);
   if(nm=="macd"){
      string fs; string sl; string sg;
      if(!ParseKV(kv, "fast", fs) || !ParseKV(kv, "slow", sl) || !ParseKV(kv, "signal", sg)) return "{\"error\":\"bad_args\"}";
      int fast=(int)StringToInteger(fs), slow=(int)StringToInteger(sl), signal=(int)StringToInteger(sg);
      int h = iMACD(symbol, tf, fast, slow, signal, PRICE_CLOSE);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true);
      int c1 = CopyBuffer(h,0,0,signal,buff1);
      int c2 = CopyBuffer(h,1,0,signal,buff2);
      if(c1<=0 || c2<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"main\":%d,\"signal\":%d}", (int)c1, (int)c2);
      double main=buff1[0], sig=buff2[0], hist=main-sig;
      return StringFormat("{\"indicator\":\"macd\",\"fast\":%d,\"slow\":%d,\"signal\":%d,\"main\":%G,\"signal_val\":%G,\"hist\":%G,\"symbol\":\"%s\"}", (int)fast, (int)slow, (int)signal, main, sig, hist, symbol);
   } else if(nm=="bbands"){
      string per; string dev; string sh;
      if(!ParseKV(kv, "period", per) || !ParseKV(kv, "deviation", dev)) return "{\"error\":\"bad_args\"}";
       int period=(int)StringToInteger(per); int deviation_int=(int)StringToDouble(dev); int shift=0; if(ParseKV(kv, "shift", sh)) shift=(int)StringToInteger(sh);
        int h=iBands(symbol, tf, period, deviation_int, shift, PRICE_CLOSE);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true); ArraySetAsSeries(buff3,true);
      int c1 = CopyBuffer(h,0,0,period,buff1);
      int c2 = CopyBuffer(h,1,0,period,buff2);
      int c3 = CopyBuffer(h,2,0,period,buff3);
      if(c1<=0 || c2<=0 || c3<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"c1\":%d,\"c2\":%d,\"c3\":%d}", (int)c1, (int)c2, (int)c3);
       return StringFormat("{\"indicator\":\"bbands\",\"period\":%d,\"deviation\":%d,\"upper\":%G,\"middle\":%G,\"lower\":%G,\"symbol\":\"%s\"}", (int)period, deviation_int, buff1[0], buff2[0], buff3[0], symbol);
   } else if(nm=="stoch"){
      string kp, dp, slw; if(!ParseKV(kv, "k_period", kp) || !ParseKV(kv, "d_period", dp) || !ParseKV(kv, "slowing", slw)) return "{\"error\":\"bad_args\"}";
      int k=(int)StringToInteger(kp), d=(int)StringToInteger(dp), s=(int)StringToInteger(slw);
      int h = iStochastic(symbol, tf, k, d, s, MODE_SMA, 0);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true);
      int c1 = CopyBuffer(h,0,0,d,buff1);
      int c2 = CopyBuffer(h,1,0,d,buff2);
      if(c1<=0 || c2<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"c1\":%d,\"c2\":%d}", (int)c1, (int)c2);
      return StringFormat("{\"indicator\":\"stoch\",\"k\":%d,\"d\":%d,\"slowing\":%d,\"k_val\":%G,\"d_val\":%G,\"symbol\":\"%s\"}", (int)k, (int)d, (int)s, buff1[0], buff2[0], symbol);
   } else if(nm=="atr"){
      string per; if(!ParseKV(kv, "period", per)) return "{\"error\":\"bad_args\"}";
      int p=(int)StringToInteger(per); int h=iATR(symbol, tf, p);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); int c1 = CopyBuffer(h,0,0,p,buff1);
      if(c1<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", (int)c1);
      return StringFormat("{\"indicator\":\"atr\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", (int)p, buff1[0], symbol);
   } else if(nm=="adx" || nm=="dmi"){
      string per; if(!ParseKV(kv, "period", per)) return "{\"error\":\"bad_args\"}";
      int p=(int)StringToInteger(per); int h=iADX(symbol, tf, p);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true); ArraySetAsSeries(buff3,true);
      int c1 = CopyBuffer(h,0,0,p,buff1);
      int c2 = CopyBuffer(h,1,0,p,buff2);
      int c3 = CopyBuffer(h,2,0,p,buff3);
      if(c1<=0 || c2<=0 || c3<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"c1\":%d,\"c2\":%d,\"c3\":%d}", (int)c1, (int)c2, (int)c3);
      return StringFormat("{\"indicator\":\"%s\",\"period\":%d,\"adx\":%G,\"plus_di\":%G,\"minus_di\":%G,\"symbol\":\"%s\"}", nm, (int)p, buff1[0], buff2[0], buff3[0], symbol);
   } else if(nm=="ichimoku"){
      string ten; string kij; string sen; if(!ParseKV(kv, "tenkan", ten) || !ParseKV(kv, "kijun", kij) || !ParseKV(kv, "senkou", sen)) return "{\"error\":\"bad_args\"}";
      int t=(int)StringToInteger(ten), k=(int)StringToInteger(kij), s=(int)StringToInteger(sen);
      int h=iIchimoku(symbol, tf, t, k, s);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true); ArraySetAsSeries(buff3,true);
      double b0[], b1[], b2[], b3[], b4[]; ArraySetAsSeries(b0,true); ArraySetAsSeries(b1,true); ArraySetAsSeries(b2,true); ArraySetAsSeries(b3,true); ArraySetAsSeries(b4,true);
      int c0 = CopyBuffer(h,0,0,t,b0);
      int c1 = CopyBuffer(h,1,0,k,b1);
      int c2 = CopyBuffer(h,2,0,s,b2);
      int c3 = CopyBuffer(h,3,0,s,b3);
      int c4 = CopyBuffer(h,4,0,s,b4);
      if(c0<=0 || c1<=0 || c2<=0 || c3<=0 || c4<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"c0\":%d,\"c1\":%d,\"c2\":%d,\"c3\":%d,\"c4\":%d}", (int)c0, (int)c1, (int)c2, (int)c3, (int)c4);
      string chikou = (b4[0] == EMPTY_VALUE || b4[0] > 1.0e307) ? "null" : DoubleToString(b4[0], (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS));
      return StringFormat("{\"indicator\":\"ichimoku\",\"tenkan\":%G,\"kijun\":%G,\"senkou_a\":%G,\"senkou_b\":%G,\"chikou\":%s,\"symbol\":\"%s\"}", b0[0], b1[0], b2[0], b3[0], chikou, symbol);
   } else if(nm=="obv"){
      int h=iOBV(symbol, tf, VOLUME_TICK);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); int c1 = CopyBuffer(h,0,0,2,buff1);
      if(c1<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", (int)c1);
      return StringFormat("{\"indicator\":\"obv\",\"value\":%G,\"symbol\":\"%s\"}", buff1[0], symbol);
   } else if(nm=="cci"){
      string per; if(!ParseKV(kv, "period", per)) return "{\"error\":\"bad_args\"}";
      int p=(int)StringToInteger(per); int h=iCCI(symbol, tf, p, PRICE_TYPICAL);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); int c1 = CopyBuffer(h,0,0,p,buff1);
      if(c1<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"copied\":%d}", (int)c1);
      return StringFormat("{\"indicator\":\"cci\",\"period\":%d,\"value\":%G,\"symbol\":\"%s\"}", (int)p, buff1[0], symbol);
   }
   return "{\"error\":\"unknown_indicator\"}";
}

string JsonTicks(const string symbol, const int count)
{
   EnsureSymbolInMarketWatch(symbol);
   MqlTick ticks[]; int copied = CopyTicks(symbol, ticks, COPY_TICKS_ALL, 0, count);
   string out = "{\"symbol\":\""+symbol+"\",\"ticks\":[";
   for(int i=copied-1;i>=0;i--){
      string item = StringFormat("{\"time_msc\":%I64d,\"bid\":%G,\"ask\":%G,\"last\":%G,\"volume\":%G,\"flags\":%u}", ticks[i].time_msc, ticks[i].bid, ticks[i].ask, ticks[i].last, ticks[i].volume, ticks[i].flags);
      out += item; if(i>0) out += ",";
   }
   out += "]}";
   return out;
}

string JsonOrderBook(const string symbol)
{
   EnsureSymbolInMarketWatch(symbol);
   bool hasBook = MarketBookAdd(symbol);
   MqlBookInfo book[];
   bool gotBook = hasBook && MarketBookGet(symbol, book);
   string bids = "["; string asks = "[";
   int bc=0, ac=0;

   if(gotBook){
      for(int i=0;i<ArraySize(book);i++){
         if(book[i].type==BOOK_TYPE_SELL){
            if(ac>0) asks += ",";
            asks += StringFormat("{\"price\":%G,\"volume\":%G}", book[i].price, book[i].volume);
            ac++;
         } else if(book[i].type==BOOK_TYPE_BUY){
            if(bc>0) bids += ",";
            bids += StringFormat("{\"price\":%G,\"volume\":%G}", book[i].price, book[i].volume);
            bc++;
         }
      }
   }

    if(bc==0 || ac==0){
       double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
       double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
       if(bc==0 && bid > 0){
          bids = StringFormat("[{\"price\":%G,\"volume\":0}]", bid);
          bc = 1;
       }
       if(ac==0 && ask > 0){
          asks = StringFormat("[{\"price\":%G,\"volume\":0}]", ask);
          ac = 1;
       }
       // Always return bid/ask fallback regardless of depth support
       return "{\"symbol\":\""+symbol+"\",\"bids\":"+bids+",\"asks\":"+asks+",\"source\":\"tick_fallback\",\"depth_supported\":true}";
    }

   bids += "]";
   asks += "]";
   return "{\"symbol\":\""+symbol+"\",\"bids\":"+bids+",\"asks\":"+asks+",\"depth_supported\":true}";
}

ENUM_TIMEFRAMES TfFromString(const string timeframe)
{
   string tf = timeframe; StringToLower(tf);
   if(tf=="m1") return PERIOD_M1; if(tf=="m5") return PERIOD_M5; if(tf=="m15") return PERIOD_M15; if(tf=="m30") return PERIOD_M30;
   if(tf=="h1") return PERIOD_H1; if(tf=="h4") return PERIOD_H4; if(tf=="d1") return PERIOD_D1; if(tf=="w1") return PERIOD_W1; if(tf=="mn1") return PERIOD_MN1;
   return PERIOD_CURRENT;
}

string ScreenshotBase64(const string symbol, const string timeframe, const int width, const int height)
{
   // Ensure symbol/timeframe context
   long chart_id = ChartID();
   ENUM_TIMEFRAMES tf = TfFromString(timeframe);
   ChartSetSymbolPeriod(chart_id, symbol, tf);
   string file = StringFormat("s_%s_%s.png", symbol, timeframe);
   bool ok = ChartScreenShot(chart_id, file, width, height, ALIGN_RIGHT);
   if(!ok) return "";
   int handle = FileOpen(file, FILE_READ|FILE_BIN|FILE_ANSI);
   if(handle==INVALID_HANDLE) return "";
   int size = (int)FileSize(handle);
   uchar bytes[]; ArrayResize(bytes,size);
   FileReadArray(handle, bytes, 0, size);
   FileClose(handle);
   uchar key[];
   uchar b64[];
   if(!CryptEncode(CRYPT_BASE64, bytes, key, b64)) return "";
   string s = CharArrayToString(b64, 0, ArraySize(b64), CP_UTF8);
   return s;
}

string JsonPositions()
{
   int total = PositionsTotal();
   string out = "{\"positions\":[";
   for(int i=0;i<total;i++){
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket)){
         string sym = PositionGetString(POSITION_SYMBOL);
         int type = (int)PositionGetInteger(POSITION_TYPE);
         double vol = PositionGetDouble(POSITION_VOLUME);
         double po = PositionGetDouble(POSITION_PRICE_OPEN);
         double pc = PositionGetDouble(POSITION_PRICE_CURRENT);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double pr = PositionGetDouble(POSITION_PROFIT);
         long t = (long)PositionGetInteger(POSITION_TIME);
         string item = StringFormat("{\"position_id\":\"%I64d\",\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%G,\"entry_price\":%G,\"mark_price\":%G,\"sl\":%G,\"tp\":%G,\"unrealized_pnl\":%G,\"opened_at\":%I64d}",
            ticket, sym, (type==POSITION_TYPE_BUY?"buy":"sell"), vol, po, pc, sl, tp, pr, t);
         out += item; if(i<total-1) out += ",";
      }
   }
   out += "]}";
   return out;
}

string JsonOrders()
{
   int total = OrdersTotal();
   string out = "{\"orders\":[";
   for(int i=0;i<total;i++){
      ulong ticket = OrderGetTicket(i);
      if(OrderSelect(ticket)){
         string sym = OrderGetString(ORDER_SYMBOL);
         int type = (int)OrderGetInteger(ORDER_TYPE);
         double vol = OrderGetDouble(ORDER_VOLUME_CURRENT);
         double price = OrderGetDouble(ORDER_PRICE_OPEN);
         double sl = OrderGetDouble(ORDER_SL);
         double tp = OrderGetDouble(ORDER_TP);
         string kind = (type==ORDER_TYPE_BUY_LIMIT||type==ORDER_TYPE_SELL_LIMIT)?"limit":((type==ORDER_TYPE_BUY_STOP||type==ORDER_TYPE_SELL_STOP)?"stop":"market");
         string side = (type==ORDER_TYPE_BUY||type==ORDER_TYPE_BUY_LIMIT||type==ORDER_TYPE_BUY_STOP)?"buy":"sell";
         string item = StringFormat("{\"order_id\":\"%I64d\",\"symbol\":\"%s\",\"side\":\"%s\",\"kind\":\"%s\",\"volume\":%G,\"price\":%G,\"sl\":%G,\"tp\":%G}",
            ticket, sym, side, kind, vol, price, sl, tp);
         out += item; if(i<total-1) out += ",";
      }
   }
   out += "]}";
   return out;
}

string JsonAccount()
{
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   string server = AccountInfoString(ACCOUNT_SERVER);
   string name = AccountInfoString(ACCOUNT_NAME);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_m  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double profit = AccountInfoDouble(ACCOUNT_PROFIT);
   double margin_level = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double margin_call = AccountInfoDouble(ACCOUNT_MARGIN_SO_CALL);
   double margin_stop_out = AccountInfoDouble(ACCOUNT_MARGIN_SO_SO);
   long leverage = AccountInfoInteger(ACCOUNT_LEVERAGE);
   return StringFormat(
      "{\"account_id\":\"%I64d\",\"name\":\"%s\",\"currency\":\"%s\",\"balance\":%G,\"equity\":%G,\"margin\":%G,\"free_margin\":%G,\"profit\":%G,\"margin_level\":%G,\"margin_call_level\":%G,\"margin_stop_out_level\":%G,\"leverage\":%I64d,\"server\":\"%s\"}",
      login, name, currency, balance, equity, margin, free_m, profit, margin_level, margin_call, margin_stop_out, leverage, server
   );
}

string TradeModeToString(const long trade_mode)
{
   if(trade_mode == SYMBOL_TRADE_MODE_DISABLED) return "disabled";
   if(trade_mode == SYMBOL_TRADE_MODE_LONGONLY) return "long_only";
   if(trade_mode == SYMBOL_TRADE_MODE_SHORTONLY) return "short_only";
   if(trade_mode == SYMBOL_TRADE_MODE_CLOSEONLY) return "close_only";
   if(trade_mode == SYMBOL_TRADE_MODE_FULL) return "full";
   return "unknown";
}

string DealEntryToString(const long entry)
{
   if(entry == DEAL_ENTRY_IN) return "in";
   if(entry == DEAL_ENTRY_OUT) return "out";
   if(entry == DEAL_ENTRY_INOUT) return "inout";
   if(entry == DEAL_ENTRY_OUT_BY) return "out_by";
   return "unknown";
}

string JsonSymbolInfo(const string symbol)
{
   if(!EnsureSymbolInMarketWatch(symbol))
      return StringFormat("{\"symbol\":\"%s\",\"error\":\"symbol_not_found\"}", symbol);

   string description = SymbolInfoString(symbol, SYMBOL_DESCRIPTION);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double contract_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double volume_min = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double volume_max = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double volume_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freeze_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int spread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   bool spread_float = (bool)SymbolInfoInteger(symbol, SYMBOL_SPREAD_FLOAT);
   long trade_mode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   long calc_mode = SymbolInfoInteger(symbol, SYMBOL_TRADE_CALC_MODE);
   string currency_base = SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE);
   string currency_profit = SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT);
   string currency_margin = SymbolInfoString(symbol, SYMBOL_CURRENCY_MARGIN);
   double swap_long = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);
   double swap_short = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT);

   return StringFormat(
      "{\"symbol\":\"%s\",\"description\":\"%s\",\"digits\":%d,\"point\":%G,\"tick_size\":%G,\"tick_value\":%G,\"contract_size\":%G,\"volume_min\":%G,\"volume_max\":%G,\"volume_step\":%G,\"stops_level_points\":%d,\"freeze_level_points\":%d,\"spread_points\":%d,\"spread_float\":%s,\"trade_mode\":\"%s\",\"calc_mode\":\"%I64d\",\"currency_base\":\"%s\",\"currency_profit\":\"%s\",\"currency_margin\":\"%s\",\"swap_long\":%G,\"swap_short\":%G}",
      symbol,
      JsonEscape(description),
      digits,
      point,
      tick_size,
      tick_value,
      contract_size,
      volume_min,
      volume_max,
      volume_step,
      stops_level,
      freeze_level,
      spread,
      (spread_float ? "true" : "false"),
      TradeModeToString(trade_mode),
      calc_mode,
      currency_base,
      currency_profit,
      currency_margin,
      swap_long,
      swap_short
   );
}

string JsonDealsHistory(const string symbol_filter, const int limit, const int days)
{
   datetime to_time = TimeCurrent();
   datetime from_time = to_time - MathMax(days, 1) * 86400;
   if(!HistorySelect(from_time, to_time))
      return "{\"deals\":[],\"error\":\"history_select_failed\"}";

   int total = HistoryDealsTotal();
   string out = "{\"deals\":[";
   int added = 0;
   for(int i = total - 1; i >= 0 && added < limit; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;

      string sym = HistoryDealGetString(ticket, DEAL_SYMBOL);
      if(symbol_filter != "" && sym != symbol_filter)
         continue;

      long deal_type = HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL)
         continue;

      string side = (deal_type == DEAL_TYPE_BUY ? "buy" : "sell");
      string entry = DealEntryToString(HistoryDealGetInteger(ticket, DEAL_ENTRY));
      ulong order_id = (ulong)HistoryDealGetInteger(ticket, DEAL_ORDER);
      ulong position_id = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
      double volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(ticket, DEAL_PRICE);
      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      double commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      double swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
      double fee = HistoryDealGetDouble(ticket, DEAL_FEE);
      long reason = HistoryDealGetInteger(ticket, DEAL_REASON);
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      long deal_time = HistoryDealGetInteger(ticket, DEAL_TIME);
      string comment = HistoryDealGetString(ticket, DEAL_COMMENT);

      string item = StringFormat(
         "{\"deal_id\":\"%I64d\",\"order_id\":\"%I64d\",\"position_id\":\"%I64d\",\"symbol\":\"%s\",\"side\":\"%s\",\"entry\":\"%s\",\"volume\":%G,\"price\":%G,\"profit\":%G,\"commission\":%G,\"swap\":%G,\"fee\":%G,\"time\":\"%I64d\",\"comment\":\"%s\",\"reason\":\"%I64d\",\"magic\":%I64d}",
         ticket,
         order_id,
         position_id,
         sym,
         side,
         entry,
         volume,
         price,
         profit,
         commission,
         swap,
         fee,
         deal_time,
         JsonEscape(comment),
         reason,
         magic
      );
      if(added > 0)
         out += ",";
      out += item;
      added++;
   }
   out += "]}";
   return out;
}

string JsonMarginEstimate(const string symbol, const string side, const double volume, const double price_hint)
{
   if(!EnsureSymbolInMarketWatch(symbol))
      return StringFormat("{\"required_margin\":0,\"comment\":\"symbol_not_found\",\"symbol\":\"%s\"}", symbol);

   ENUM_ORDER_TYPE order_type = ORDER_TYPE_BUY;
   string side_lower = side;
   StringToLower(side_lower);
   if(side_lower == "sell")
      order_type = ORDER_TYPE_SELL;

   double market_price = price_hint;
   if(market_price <= 0)
      market_price = (side_lower == "buy" ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID));

   double required_margin = 0.0;
   bool ok = OrderCalcMargin(order_type, symbol, volume, market_price, required_margin);
   long leverage = AccountInfoInteger(ACCOUNT_LEVERAGE);
   if(!ok)
      return StringFormat("{\"required_margin\":0,\"leverage\":%I64d,\"comment\":\"order_calc_margin_failed\",\"symbol\":\"%s\",\"price\":%G}", leverage, symbol, market_price);

   return StringFormat("{\"required_margin\":%G,\"leverage\":%I64d,\"comment\":\"ok\",\"symbol\":\"%s\",\"price\":%G}", required_margin, leverage, symbol, market_price);
}

string PositionModifySLTPByTicket(const ulong ticket, const double sl_input, const double tp_input, bool &ok_out)
{
   ok_out = false;
   if(!PositionSelectByTicket(ticket))
      return StringFormat("{\"position_id\":\"%I64d\",\"error\":\"position_not_found\"}", ticket);

   string sym = PositionGetString(POSITION_SYMBOL);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int stops_level = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   int position_type = (int)PositionGetInteger(POSITION_TYPE);
   double sl = sl_input;
   double tp = tp_input;

   if(sl > 0)
      sl = NormalizeDouble(sl, digits);
   if(tp > 0)
      tp = NormalizeDouble(tp, digits);

   double market_price = (position_type == POSITION_TYPE_BUY ? bid : ask);
   double min_distance = stops_level * point;
   if(sl > 0 && min_distance > 0 && MathAbs(market_price - sl) < min_distance)
      return StringFormat("{\"position_id\":\"%I64d\",\"symbol\":\"%s\",\"retcode\":10016,\"retcode_description\":\"INVALID_STOPS\",\"sl\":%G,\"tp\":%G}", ticket, sym, sl, tp);
   if(tp > 0 && min_distance > 0 && MathAbs(tp - market_price) < min_distance)
      return StringFormat("{\"position_id\":\"%I64d\",\"symbol\":\"%s\",\"retcode\":10016,\"retcode_description\":\"INVALID_STOPS\",\"sl\":%G,\"tp\":%G}", ticket, sym, sl, tp);

   CTrade trade;
   bool ok = trade.PositionModify(ticket, sl, tp);
   uint retcode = trade.ResultRetcode();
   string payload = StringFormat(
      "{\"position_id\":\"%I64d\",\"symbol\":\"%s\",\"retcode\":%u,\"retcode_description\":\"%s\",\"sl\":%G,\"tp\":%G}",
      ticket,
      sym,
      retcode,
      JsonEscape(trade.ResultRetcodeDescription()),
      sl,
      tp
   );
   ok_out = ok && (retcode == 10009 || retcode == 10025);
   return payload;
}

bool PositionCloseByTicket(const ulong ticket, const double vol)
{
   if(!PositionSelectByTicket(ticket)) return false;
   string sym = PositionGetString(POSITION_SYMBOL);
   CTrade trade; if(vol>0) return trade.PositionClosePartial(sym, vol);
   return trade.PositionClose(sym);
}

bool OrderDeleteByTicket(const ulong ticket)
{
   CTrade trade; return trade.OrderDelete(ticket);
}

bool OrderModifyByTicket(const ulong ticket, const double price, const double sl, const double tp)
{
   CTrade trade;
   return trade.OrderModify(ticket, price, sl, tp, ORDER_TIME_GTC, 0, 0);
}

// ============================================================
// MQL5 Native Economic Calendar API
// Uses CalendarValueHistory (bulk fetch) + event definition lookup.
// 
// CORRECT APPROACH (per MQL5 docs):
// 1. Build event_id -> event definition map from all countries
// 2. Use CalendarValueHistory() to bulk-fetch ALL values from now onward
//    (with datetime_to=0 to include scheduled future events)
// 3. Join values with event definitions for complete output
//
// WHY NOT CalendarValueHistoryByEvent per event?
// - N+1 query problem (23 countries x ~50 events = 1150+ API calls)
// - Future events may not have value entries populated yet
// - CalendarValueHistory is designed for this exact use case
// ============================================================

string CalendarImportanceToString(ENUM_CALENDAR_EVENT_IMPORTANCE imp)
{
   switch(imp)
   {
      case CALENDAR_IMPORTANCE_HIGH:    return "HIGH";
      case CALENDAR_IMPORTANCE_MODERATE: return "MEDIUM";
      case CALENDAR_IMPORTANCE_LOW:     return "LOW";
      default:                          return "NONE";
   }
}

string CalendarEventTypeToString(ENUM_CALENDAR_EVENT_TYPE t)
{
   switch(t)
   {
      case CALENDAR_TYPE_EVENT:         return "event";
      case CALENDAR_TYPE_INDICATOR:     return "indicator";
      case CALENDAR_TYPE_HOLIDAY:       return "holiday";
      default:                          return "unknown";
   }
}

string CalendarFrequencyToString(ENUM_CALENDAR_EVENT_FREQUENCY f)
{
   switch(f)
   {
      case CALENDAR_FREQUENCY_NONE:      return "none";
      case CALENDAR_FREQUENCY_WEEK:      return "weekly";
      case CALENDAR_FREQUENCY_MONTH:     return "monthly";
      case CALENDAR_FREQUENCY_QUARTER:   return "quarterly";
      case CALENDAR_FREQUENCY_YEAR:      return "yearly";
      case CALENDAR_FREQUENCY_DAY:       return "daily";
      default:                          return "unknown";
   }
}

// --- Event definition cache (avoids repeated CalendarEventByCountry calls) ---
struct SEventDef
{
   ulong   id;
   string  name;
   string  country_name;
   string  country_code;
   string  currency;
   ENUM_CALENDAR_EVENT_IMPORTANCE importance;
   ENUM_CALENDAR_EVENT_TYPE       type;
   ENUM_CALENDAR_EVENT_FREQUENCY  frequency;
};

// Forward declarations (MQL5 requires declarations before use)
string NormalizeCurrency(const string raw);
string CalendarFormatValue(const long raw_value);

SEventDef g_event_cache[];
int       g_event_cache_size = 0;
bool      g_event_cache_ready = false;

void CalendarBuildEventCache()
{
   if(g_event_cache_ready) return;
   
   MqlCalendarCountry countries[];
   if(!CalendarCountries(countries))
   {
      Print("CalendarBuildEventCache: CalendarCountries failed, error=", GetLastError());
      return;
   }
   
   // First pass: count total events to size the cache array
   int total_events = 0;
   for(int ci = 0; ci < ArraySize(countries); ci++)
   {
      MqlCalendarEvent evts[];
      int cnt = CalendarEventByCountry(countries[ci].code, evts);
      if(cnt > 0) total_events += cnt;
   }
   
   if(total_events == 0)
   {
      Print("CalendarBuildEventCache: No events found across ", ArraySize(countries), " countries");
      return;
   }
   
   ArrayResize(g_event_cache, total_events);
   g_event_cache_size = 0;
   
   // Second pass: populate cache
   for(int ci = 0; ci < ArraySize(countries); ci++)
   {
      string curr = NormalizeCurrency(countries[ci].name);
      MqlCalendarEvent evts[];
      int cnt = CalendarEventByCountry(countries[ci].code, evts);
      if(cnt <= 0) continue;
      
      for(int ei = 0; ei < cnt; ei++)
      {
         int idx = g_event_cache_size;
         g_event_cache[idx].id = evts[ei].id;
         g_event_cache[idx].name = evts[ei].name;
         g_event_cache[idx].country_name = countries[ci].name;
         g_event_cache[idx].country_code = countries[ci].code;
         g_event_cache[idx].currency = curr;
         g_event_cache[idx].importance = evts[ei].importance;
         g_event_cache[idx].type = evts[ei].type;
         g_event_cache[idx].frequency = evts[ei].frequency;
         g_event_cache_size++;
      }
   }
   
   g_event_cache_ready = true;
   Print("CalendarBuildEventCache: Built cache with ", g_event_cache_size, " events across ", ArraySize(countries), " countries");
}

// Look up event definition by ID from the cache
bool CalendarLookupEvent(const ulong event_id, SEventDef &out_def)
{
   for(int i = 0; i < g_event_cache_size; i++)
   {
      if(g_event_cache[i].id == event_id)
      {
         out_def = g_event_cache[i];
         return true;
      }
   }
   return false;
}

// Map MQL5 country names to forex-friendly currency codes
string NormalizeCurrency(const string raw)
{
   string c = raw;
   if(c=="United States") return "USD";
   if(c=="Eurozone") return "EUR";
   if(c=="United Kingdom") return "GBP";
   if(c=="Japan") return "JPY";
   if(c=="Switzerland") return "CHF";
   if(c=="Canada") return "CAD";
   if(c=="Australia") return "AUD";
   if(c=="New Zealand") return "NZD";
   if(c=="China") return "CNY";
   if(c=="Germany") return "EUR";
   if(c=="France") return "EUR";
   if(c=="Italy") return "EUR";
   if(c=="Spain") return "EUR";
   if(c=="Brazil") return "BRL";
   if(c=="India") return "INR";
   if(c=="Mexico") return "MXN";
   if(c=="South Korea") return "KRW";
   if(c=="Singapore") return "SGD";
   if(c=="Hong Kong") return "HKD";
   if(c=="Sweden") return "SEK";
   if(c=="Norway") return "NOK";
   if(c=="Denmark") return "DKK";
   if(c=="Poland") return "PLN";
   if(c=="Russia") return "RUB";
   if(c=="Turkey") return "TRY";
   if(c=="South Africa") return "ZAR";
   if(c=="Israel") return "ILS";
   return c;
}

// Safe value formatting: MQL5 stores calendar values scaled by 1,000,000.
// LONG_MIN means the value is not set (empty).
string CalendarFormatValue(const long raw_value)
{
   // MQL5 uses LONG_MIN for unset values (not EMPTY_VALUE or 0)
   if(raw_value == LONG_MIN) return "null";
   if(raw_value == 0) return "0";
   // Divide by 1,000,000 to get the real value per MQL5 docs
   return StringFormat("%.6G", (double)raw_value / 1000000.0);
}

string JsonCalendar(const string currency_filter, const int hours_ahead, const string min_impact)
{
   datetime now = TimeCurrent();
   // datetime_to=0 means "all values from now onward, including scheduled future events"
   // Per MQL5 docs: "If datetime_to is not set (or is 0), all event values beginning from
   // datetime_from are returned (including the values of future events)"
   
   // Determine minimum importance level
   ENUM_CALENDAR_EVENT_IMPORTANCE min_imp = CALENDAR_IMPORTANCE_LOW;
   string mi = min_impact;
   StringToUpper(mi);
   if(mi == "HIGH")      min_imp = CALENDAR_IMPORTANCE_HIGH;
   else if(mi == "MEDIUM" || mi == "MODERATE") min_imp = CALENDAR_IMPORTANCE_MODERATE;
   
   // Build event definition cache (cached across calls)
   CalendarBuildEventCache();
   
   if(g_event_cache_size == 0)
   {
      return StringFormat("{\"error\":\"no_events_in_cache\",\"note\":\"CalendarCountries or CalendarEventByCountry returned 0 events. Check terminal connection.\"}");
   }
   
   // Use CalendarValueHistory for BULK fetch — one call gets ALL values across ALL events
   // This is MUCH faster than N+1 CalendarValueHistoryByEvent calls
   MqlCalendarValue all_values[];
   int total_values = CalendarValueHistory(all_values, now, 0, NULL, NULL);
   
   if(total_values <= 0)
   {
      int err = GetLastError();
      return StringFormat(
         "{\"events\":[],\"event_count\":0,\"calendar_query_info\":{\"total_values_returned\":0,\"error_code\":%d,\"error_desc\":\"%s\",\"cache_events\":%d,\"time_from\":\"%s\",\"note\":\"CalendarValueHistory returned 0. This may indicate no scheduled events in the calendar database from now onward.\"}}",
         err,
         ErrorDescription(err),
         g_event_cache_size,
         TimeToString(now, TIME_DATE|TIME_SECONDS)
      );
   }
   
   Print("Calendar: CalendarValueHistory returned ", total_values, " values. Filtering...");
   
   // Process values: join with event definitions, filter by importance and currency
   string events_json = "";
   int event_count = 0;
   
   // We need to collect unique values (same event_id can have multiple time entries)
   // For each value, look up the event definition and apply filters
   for(int vi = 0; vi < ArraySize(all_values); vi++)
   {
      SEventDef evt_def;
      if(!CalendarLookupEvent(all_values[vi].event_id, evt_def))
         continue;
      
      // Filter by importance
      if(evt_def.importance < min_imp)
         continue;
      
      // Filter by currency
      if(currency_filter != "" && currency_filter != "ALL")
      {
         if(evt_def.currency != currency_filter)
            continue;
      }
      
      // Skip values outside our time window (future = now + hours_ahead)
      datetime future = now + (hours_ahead * 3600);
      if(all_values[vi].time > future)
         continue;
      
      // Format values (MQL5 stores them scaled by 1,000,000)
      string actual_str = CalendarFormatValue(all_values[vi].actual_value);
      string forecast_str = CalendarFormatValue(all_values[vi].forecast_value);
      string previous_str = CalendarFormatValue(all_values[vi].prev_value);
      string revised_str = CalendarFormatValue(all_values[vi].revision);
      
      // Check if this is a future event (actual_value is LONG_MIN = not yet published)
      bool is_future = (all_values[vi].actual_value == LONG_MIN);
      
      string evt = StringFormat(
         "{"
         "\"event_id\":\"%I64u\","
         "\"value_id\":\"%I64u\","
         "\"name\":\"%s\","
         "\"country\":\"%s\","
         "\"country_code\":\"%s\","
         "\"currency\":\"%s\","
         "\"importance\":\"%s\","
         "\"type\":\"%s\","
         "\"frequency\":\"%s\","
         "\"time\":\"%s\","
         "\"timestamp\":%I64d,"
         "\"actual\":%s,"
         "\"forecast\":%s,"
         "\"previous\":%s,"
         "\"revision\":%s,"
         "\"is_future\":%s,"
         "\"impact_type\":%d"
         "}",
         evt_def.id,
         all_values[vi].id,
         JsonEscape(evt_def.name),
         JsonEscape(evt_def.country_name),
         evt_def.country_code,
         evt_def.currency,
         CalendarImportanceToString(evt_def.importance),
         CalendarEventTypeToString(evt_def.type),
         CalendarFrequencyToString(evt_def.frequency),
         TimeToString(all_values[vi].time, TIME_DATE|TIME_SECONDS),
         (long)all_values[vi].time,
         actual_str,
         forecast_str,
         previous_str,
         revised_str,
         is_future ? "true" : "false",
         (int)all_values[vi].impact_type
      );
      
      if(events_json != "") events_json += ",";
      events_json += evt;
      event_count++;
   }
   
   datetime future = now + (hours_ahead * 3600);
   
   // Build response
   string result = StringFormat(
      "{"
      "\"events\":[%s],"
      "\"event_count\":%d,"
      "\"time_range\":{"
      "\"from\":\"%s\","
      "\"to\":\"%s\""
      "},"
      "\"source\":\"mt5_terminal_calendar\","
      "\"currency_filter\":\"%s\","
      "\"min_impact\":\"%s\","
      "\"total_events_in_cache\":%d,"
      "\"total_values_from_api\":%d,"
      "\"note\":\"Values scaled by 1,000,000 per MQL5 spec. is_future=true means actual not yet published.\""
      "}",
      events_json,
      event_count,
      TimeToString(now, TIME_DATE|TIME_SECONDS),
      TimeToString(future, TIME_DATE|TIME_SECONDS),
      currency_filter,
      min_impact,
      g_event_cache_size,
      total_values
   );
   
   Print("Calendar: Returning ", event_count, " events (from ", total_values, " values, ", g_event_cache_size, " event definitions)");
   
   return result;
}

void Complete(const string request_id, const string payload_json)
{
   string url = GatewayBaseURL + "/bridge/results";
   string body = StringFormat("{\"request_id\":\"%s\",\"status\":\"ok\",\"payload\":%s}", request_id, payload_json);
   string resp;
   int code = HttpPost(url, body, resp);
   if(code != 200)
      Print("BridgeConnectorEA: result callback failed request_id=", request_id, " code=", code, " last_error=", GetLastError(), " response=", resp);
   else if(EnableDebugLogs)
      Print("BridgeConnectorEA: completed request_id=", request_id);
}

void Fail(const string request_id, const string message)
{
   string url = GatewayBaseURL + "/bridge/results";
   string body = StringFormat("{\"request_id\":\"%s\",\"status\":\"error\",\"error\":\"%s\"}", request_id, JsonEscape(message));
   Print("BridgeConnectorEA: request failed request_id=", request_id, " error=", message);
   string resp; int __code = HttpPost(url, body, resp); 
   if(__code != 200)
      Print("BridgeConnectorEA: failure callback failed request_id=", request_id, " code=", __code, " last_error=", GetLastError(), " response=", resp);
}

void ProcessCommand(const string cmd)
{
   if(cmd=="NONE" || cmd=="")
      return;
   DebugLog(StringFormat("DEBUG_PROCESSCMD: RAW_CMD=[%s]", cmd));
   string rid; if(!ParseKV(cmd, "request_id", rid)) {
      Print("BridgeConnectorEA: missing request_id in command [", cmd, "]");
      return;
   }
   string type; if(!ParseKV(cmd, "type", type)) { 
      Print("BridgeConnectorEA: missing type for request_id=", rid);
      Fail(rid, "missing_type"); 
      return; 
   }
   DebugLog(StringFormat("DEBUG_PROCESSCMD: request_id=[%s] type=[%s]", rid, type));
   if(type=="get_bars"){
      string sym; string tf; string cnts;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "timeframe", tf) || !ParseKV(cmd, "count", cnts)) { Fail(rid, "bad_args"); return; }
      int cnt = (int)StringToInteger(cnts);
      string payload = JsonBars(sym, tf, cnt);
      Complete(rid, payload);
   } else if(type=="get_indicator"){
      string sym; string tf; string name; string ps;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "timeframe", tf) || !ParseKV(cmd, "indicator", name)) { Fail(rid, "bad_args"); return; }
      // Try advanced first using full kv string, fallback to simple period-only
      string payload = JsonIndicatorAdvanced(sym, tf, name, cmd);
      if(StringFind(payload, "unknown_indicator")>=0 || StringFind(payload, "bad_args")>=0){
         if(!ParseKV(cmd, "period", ps)) { Fail(rid, "bad_args"); return; }
         int period = (int)StringToInteger(ps);
         payload = JsonIndicator(sym, tf, name, period);
      }
      Complete(rid, payload);
   } else if(type=="get_chart_screenshot"){
      string sym; string tf; string ws; string hs;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "timeframe", tf) || !ParseKV(cmd, "width", ws) || !ParseKV(cmd, "height", hs)) { Fail(rid, "bad_args"); return; }
      int w = (int)StringToInteger(ws); int h = (int)StringToInteger(hs);
      string b64 = ScreenshotBase64(sym, tf, w, h);
      string payload = StringFormat("{\"image_base64\":\"%s\",\"content_type\":\"image/png\"}", b64);
      Complete(rid, payload);
   } else if(type=="get_ticks"){
      string sym; string cnts; if(!ParseKV(cmd, "symbol", sym)) { Fail(rid, "bad_args"); return; }
      int cnt=200; if(ParseKV(cmd, "count", cnts)) cnt=(int)StringToInteger(cnts);
      string payload = JsonTicks(sym, cnt);
      Complete(rid, payload);
   } else if(type=="get_order_book"){
      string sym; if(!ParseKV(cmd, "symbol", sym)) { Fail(rid, "bad_args"); return; }
      string payload = JsonOrderBook(sym);
      Complete(rid, payload);
   } else if(type=="get_symbol_info"){
      string sym; if(!ParseKV(cmd, "symbol", sym)) { Fail(rid, "bad_args"); return; }
      Complete(rid, JsonSymbolInfo(sym));
   } else if(type=="get_deals_history"){
      string sym; string lims; string ds;
      string symbol_filter = "";
      int limit = 100;
      int days = 30;
      if(ParseKV(cmd, "symbol", sym)) symbol_filter = sym;
      if(ParseKV(cmd, "limit", lims)) limit = (int)StringToInteger(lims);
      if(ParseKV(cmd, "days", ds)) days = (int)StringToInteger(ds);
      Complete(rid, JsonDealsHistory(symbol_filter, limit, days));
   } else if(type=="estimate_margin"){
      string sym; string side; string vols; string ps;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "side", side) || !ParseKV(cmd, "volume_lots", vols)) { Fail(rid, "bad_args"); return; }
      double volume = StringToDouble(vols);
      double price_hint = 0.0;
      if(ParseKV(cmd, "price", ps)) price_hint = StringToDouble(ps);
      Complete(rid, JsonMarginEstimate(sym, side, volume, price_hint));
    } else if(type=="submit_order"){
      string sym; string side; string vols; string sls; string tps; string devs;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "side", side) || !ParseKV(cmd, "volume_lots", vols)) { Fail(rid, "bad_args"); return; }
      
      // Symbol validation
      if(!EnsureSymbolInMarketWatch(sym)) { Fail(rid, "symbol_not_found"); return; }
      
      double vol = StringToDouble(vols);
      double sl = 0.0; double tp = 0.0; int dev=20;
      if(ParseKV(cmd, "sl", sls)) sl = StringToDouble(sls);
      if(ParseKV(cmd, "tp", tps)) tp = StringToDouble(tps);
      if(ParseKV(cmd, "deviation", devs)) dev = (int)StringToInteger(devs);
      
      // Get current prices for SL/TP validation
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      if(ask==0 || bid==0) { Fail(rid, "invalid_prices"); return; }
      
      // Validate SL/TP distances (minimum 10 points)
      double min_distance = 10.0 * point;
      if(sl > 0) {
         double sl_distance = MathAbs((side=="buy" ? bid - sl : sl - ask));
         if(sl_distance < min_distance) { Fail(rid, "sl_too_close"); return; }
      }
      if(tp > 0) {
         double tp_distance = MathAbs((side=="buy" ? tp - ask : bid - tp));
         if(tp_distance < min_distance) { Fail(rid, "tp_too_close"); return; }
      }
      
      string side_lower = side;
      StringToLower(side_lower);
      ENUM_ORDER_TYPE ot = (side_lower=="buy" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      
      // Detect supported filling mode
      int filling_mask = (int)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
      ENUM_ORDER_TYPE_FILLING filling = ORDER_FILLING_FOK;
       if((filling_mask & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK) filling = ORDER_FILLING_FOK;
       else if((filling_mask & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC) filling = ORDER_FILLING_IOC;
      
      MqlTradeRequest req; MqlTradeResult res; ZeroMemory(req); ZeroMemory(res);
      req.action = TRADE_ACTION_DEAL;
      req.symbol = sym;
      req.volume = vol;
      req.type = ot;
      req.type_filling = filling;
      req.deviation = dev;
      if(sl>0) req.sl = sl; if(tp>0) req.tp = tp;
      bool ok = OrderSend(req, res);
      string payload = StringFormat("{\"retcode\":%d,\"order\":%I64d,\"deal\":%I64d,\"ask\":%G,\"bid\":%G,\"filling\":\"%s\"}", res.retcode, res.order, res.deal, ask, bid, EnumToString(filling));
      if(ok && res.retcode==10009 /*TRADE_RETCODE_DONE*/)
         Complete(rid, payload);
      else
         Fail(rid, payload);
   } else if(type=="get_positions"){
      Complete(rid, JsonPositions());
   } else if(type=="get_orders"){
      Complete(rid, JsonOrders());
   } else if(type=="get_account"){
      Complete(rid, JsonAccount());
   } else if(type=="modify_position_sl_tp"){
      string tk; string sls; string tps;
      if(!ParseKV(cmd, "position_id", tk)) { Fail(rid, "bad_args"); return; }
      ulong ticket = (ulong)StringToInteger(tk);
      double sl=0, tp=0; if(ParseKV(cmd, "sl", sls)) sl=StringToDouble(sls); if(ParseKV(cmd, "tp", tps)) tp=StringToDouble(tps);
      bool ok = false;
      string payload = PositionModifySLTPByTicket(ticket, sl, tp, ok);
      if(ok) Complete(rid, payload); else Fail(rid, payload);
   } else if(type=="close_position"){
      string tk; string vs;
      if(!ParseKV(cmd, "position_id", tk)) { Fail(rid, "bad_args"); return; }
      double vol=0; if(ParseKV(cmd, "volume", vs)) vol=StringToDouble(vs);
      ulong ticket = (ulong)StringToInteger(tk);
      bool ok = PositionCloseByTicket(ticket, vol);
      if(ok) Complete(rid, "{\"status\":\"ok\"}"); else Fail(rid, "close_failed");
   } else if(type=="submit_pending_order"){
       string sym; string side; string kind; string ps; string vols; string sls; string tps; string devs;
       if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "side", side) || !ParseKV(cmd, "kind", kind) || !ParseKV(cmd, "price", ps) || !ParseKV(cmd, "volume_lots", vols)) { Fail(rid, "bad_args"); return; }
       if(!EnsureSymbolInMarketWatch(sym)) { Fail(rid, "symbol_not_found"); return; }
       double price = StringToDouble(ps); double vol = StringToDouble(vols);
       double sl=0, tp=0; int dev=20; if(ParseKV(cmd, "sl", sls)) sl=StringToDouble(sls); if(ParseKV(cmd, "tp", tps)) tp=StringToDouble(tps); if(ParseKV(cmd, "deviation", devs)) dev=(int)StringToInteger(devs);
       int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
       price = NormalizeDouble(price, digits);
       if(sl > 0) sl = NormalizeDouble(sl, digits);
       if(tp > 0) tp = NormalizeDouble(tp, digits);
       CTrade trade; trade.SetDeviationInPoints(dev); trade.SetTypeFillingBySymbol(sym); bool ok=false;
       string s = side; StringToLower(s); string k = kind; StringToLower(k);
       if(s=="buy" && k=="limit") ok=trade.BuyLimit(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
       else if(s=="sell" && k=="limit") ok=trade.SellLimit(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
       else if(s=="buy" && k=="stop") ok=trade.BuyStop(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
       else if(s=="sell" && k=="stop") ok=trade.SellStop(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
       uint retcode = trade.ResultRetcode();
       string payload = StringFormat("{\"retcode\":%u,\"retcode_description\":\"%s\",\"order\":%I64d,\"deal\":%I64d,\"price\":%G,\"volume\":%G,\"symbol\":\"%s\"}",
          retcode,
          JsonEscape(trade.ResultRetcodeDescription()),
          trade.ResultOrder(),
          trade.ResultDeal(),
          price,
          vol,
          sym);
       if(ok && (retcode==10008 || retcode==10009)) Complete(rid, payload); else Fail(rid, payload);
   } else if(type=="cancel_order"){
      string okid; if(!ParseKV(cmd, "order_id", okid)) { Fail(rid, "bad_args"); return; }
      ulong ticket = (ulong)StringToInteger(okid);
      bool ok = OrderDeleteByTicket(ticket);
      if(ok) Complete(rid, "{\"status\":\"ok\"}"); else Fail(rid, "cancel_failed");
   } else if(type=="modify_order"){
      string okid; string ps; string sls; string tps;
      if(!ParseKV(cmd, "order_id", okid)) { Fail(rid, "bad_args"); return; }
      double price=0, sl=0, tp=0; if(ParseKV(cmd, "new_price", ps)) price=StringToDouble(ps); if(ParseKV(cmd, "new_sl", sls)) sl=StringToDouble(sls); if(ParseKV(cmd, "new_tp", tps)) tp=StringToDouble(tps);
      ulong ticket = (ulong)StringToInteger(okid);
      bool ok = OrderModifyByTicket(ticket, price, sl, tp);
      if(ok) Complete(rid, "{\"status\":\"ok\"}"); else Fail(rid, "modify_order_failed");
   } else if(type=="close_all_positions"){
      string sym; string side;
      bool hasSym = ParseKV(cmd, "symbol", sym);
      bool hasSide = ParseKV(cmd, "side", side);
      int total = PositionsTotal(); int okc=0; int errc=0;
      for(int i=0;i<total;i++){
         ulong tk = PositionGetTicket(i);
         if(PositionSelectByTicket(tk)){
            string psym = PositionGetString(POSITION_SYMBOL);
            int ptype = (int)PositionGetInteger(POSITION_TYPE);
            string pside = (ptype==POSITION_TYPE_BUY?"buy":"sell");
            if(hasSym && psym!=sym) continue;
            if(hasSide && side!="both" && pside!=side) continue;
            if(PositionCloseByTicket(tk, 0)) okc++; else errc++;
         }
      }
      string resp = StringFormat("{\"closed\":%d,\"failed\":%d}", okc, errc);
      Complete(rid, resp);
    } else if(type=="cancel_all_orders"){
       string sym; string side;
       bool hasSym = ParseKV(cmd, "symbol", sym);
       bool hasSide = ParseKV(cmd, "side", side);
       int total = OrdersTotal(); int okc=0; int errc=0;
       for(int i=0;i<total;i++){
          ulong tk = OrderGetTicket(i);
          if(OrderSelect(tk)){
             string osym = OrderGetString(ORDER_SYMBOL);
             int otype = (int)OrderGetInteger(ORDER_TYPE);
             string oside = (otype==ORDER_TYPE_BUY||otype==ORDER_TYPE_BUY_LIMIT||otype==ORDER_TYPE_BUY_STOP)?"buy":"sell";
             if(hasSym && osym!=sym) continue;
             if(hasSide && side!="both" && oside!=side) continue;
             if(OrderDeleteByTicket(tk)) okc++; else errc++;
          }
       }
       string resp = StringFormat("{\"cancelled\":%d,\"failed\":%d}", okc, errc);
       Complete(rid, resp);
    } else if(type=="get_calendar"){
       string cur=""; string has="24"; string mi="MEDIUM";
       ParseKV(cmd, "currency", cur);
       ParseKV(cmd, "hours_ahead", has);
       ParseKV(cmd, "min_impact", mi);
       int ha = (int)StringToInteger(has);
       if(ha < 1) ha = 24;
       Complete(rid, JsonCalendar(cur, ha, mi));
    } else {
       Fail(rid, "unknown_command");
    }
 }
