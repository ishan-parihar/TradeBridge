//+------------------------------------------------------------------+
//|                                                BridgeConnectorEA |
//|                             MT5 ↔ MCP EA bridge (heartbeat)       |
//+------------------------------------------------------------------+
#property copyright "MT5-mcp"
#property version   "2.20"

// Trading includes
#include <Trade\Trade.mqh>

// Inputs
input string GatewayBaseURL = "http://127.0.0.1:8020";
input string GatewayURL = "http://127.0.0.1:8020/bridge/terminal/heartbeat"; // deprecated, use GatewayBaseURL
input int    HeartbeatSeconds = 5;

// Internal state
int g_last_status = 0;

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
   
   Print("DEBUG: login=", login, " server=", server, " build=", build);
   
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
   Print("DEBUG_ONTIMER: Calling NextCommand");
   string cmd = NextCommand();
   Print("DEBUG_ONTIMER: NextCommand returned, calling ProcessCommand");
   ProcessCommand(cmd);
   Print("DEBUG_ONTIMER: ProcessCommand completed");
}

void SendHeartbeat()
{
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   bool trade_connected = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   bool connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   
   Print("DEBUG HEARTBEAT: login=", login, " connected=", connected, " trade_allowed=", trade_connected);
   
   string json = BuildHeartbeatJson();
   string resp;
   int code = HttpPost(GatewayURL, json, resp);
   g_last_status = code;
   if(code != 200)
      Print("Heartbeat failed code=", code, " last_error=", GetLastError(), " response=", resp);
   else
      Print("DEBUG: Heartbeat OK - login=", login, " server=", AccountInfoString(ACCOUNT_SERVER));
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
   Print("DEBUG_NEXTCMD: url=[", url, "] response=[", resp, "]");
   return resp;
}

bool ParseKV(const string s, const string key, string &value)
{
   Print("DEBUG_PARSEKV: input=[", s, "] key=[", key, "]");
   string parts[];
   int n = StringSplit(s, '&', parts);
   Print("DEBUG_PARSEKV: parts_count=", n);
   for(int i=0;i<n;i++){
      Print("DEBUG_PARSEKV: part[", i, "]=[", parts[i], "]");
      string kv[]; int m = StringSplit(parts[i], '=', kv);
      Print("DEBUG_PARSEKV: split_count=", m);
      if(m>=1) Print("DEBUG_PARSEKV: kv[0]=[", kv[0], "]");
      if(m>=2) Print("DEBUG_PARSEKV: kv[1]=[", kv[1], "]");
      if(m==2 && kv[0]==key){ 
         Print("DEBUG_PARSEKV: MATCH found! key=[", key, "] value=[", kv[1], "]");
         value = kv[1]; 
         return true; 
      }
   }
   Print("DEBUG_PARSEKV: NO MATCH for key=[", key, "]");
   return false;
}

bool EnsureSymbolInMarketWatch(const string symbol)
{
   if(SymbolSelect(symbol, true))
      return true;
   
   Print("BridgeConnectorEA: Added symbol ", symbol, " to Market Watch");
   return true;
}

string JsonBars(const string symbol, const string timeframe, const int count)
{
   EnsureSymbolInMarketWatch(symbol);
   
   ENUM_TIMEFRAMES tf = PERIOD_M1;
   if(timeframe=="M5") tf=PERIOD_M5; else if(timeframe=="M15") tf=PERIOD_M15; else if(timeframe=="M30") tf=PERIOD_M30;
   else if(timeframe=="H1") tf=PERIOD_H1; else if(timeframe=="H4") tf=PERIOD_H4; else if(timeframe=="D1") tf=PERIOD_D1;
   
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
      int period=(int)StringToInteger(per); double deviation=StringToDouble(dev); int shift=0; if(ParseKV(kv, "shift", sh)) shift=(int)StringToInteger(sh);
      int h=iBands(symbol, tf, period, deviation, shift, PRICE_CLOSE);
      if(h==INVALID_HANDLE) return StringFormat("{\"error\":\"indicator_handle_failed\",\"symbol\":\"%s\"}", symbol);
      ArraySetAsSeries(buff1,true); ArraySetAsSeries(buff2,true); ArraySetAsSeries(buff3,true);
      int c1 = CopyBuffer(h,0,0,period,buff1);
      int c2 = CopyBuffer(h,1,0,period,buff2);
      int c3 = CopyBuffer(h,2,0,period,buff3);
      if(c1<=0 || c2<=0 || c3<=0) return StringFormat("{\"error\":\"copy_buffer_failed\",\"c1\":%d,\"c2\":%d,\"c3\":%d}", (int)c1, (int)c2, (int)c3);
      return StringFormat("{\"indicator\":\"bbands\",\"period\":%d,\"deviation\":%G,\"upper\":%G,\"middle\":%G,\"lower\":%G,\"symbol\":\"%s\"}", (int)period, deviation, buff1[0], buff2[0], buff3[0], symbol);
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
      return StringFormat("{\"indicator\":\"ichimoku\",\"tenkan\":%G,\"kijun\":%G,\"senkou_a\":%G,\"senkou_b\":%G,\"chikou\":%G,\"symbol\":\"%s\"}", b0[0], b1[0], b2[0], b3[0], b4[0], symbol);
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
   MqlBookInfo book[]; if(!MarketBookGet(symbol, book)) return "{\"symbol\":\""+symbol+"\",\"bids\":[],\"asks\":[]}";
   string bids = "["; string asks = "[";
   int bc=0, ac=0;
   for(int i=0;i<ArraySize(book);i++){
      if(book[i].type==BOOK_TYPE_SELL){
         if(ac>0) asks += ","; asks += StringFormat("{\"price\":%G,\"volume\":%G}", book[i].price, book[i].volume); ac++;
      } else if(book[i].type==BOOK_TYPE_BUY){
         if(bc>0) bids += ","; bids += StringFormat("{\"price\":%G,\"volume\":%G}", book[i].price, book[i].volume); bc++;
      }
   }
   bids += "]"; asks += "]";
   return "{\"symbol\":\""+symbol+"\",\"bids\":"+bids+",\"asks\":"+asks+"}";
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
   return StringFormat("{\"account_id\":\"%I64d\",\"name\":\"%s\",\"currency\":\"%s\",\"balance\":%G,\"equity\":%G,\"margin\":%G,\"free_margin\":%G,\"server\":\"%s\"}", login, name, currency, balance, equity, margin, free_m, server);
}

bool PositionModifySLTPByTicket(const ulong ticket, const double sl, const double tp)
{
   if(!PositionSelectByTicket(ticket)) return false;
   string sym = PositionGetString(POSITION_SYMBOL);
   CTrade trade; return trade.PositionModify(sym, sl, tp);
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

void Complete(const string request_id, const string payload_json)
{
   string url = GatewayBaseURL + "/bridge/results";
   string body = StringFormat("{\"request_id\":\"%s\",\"status\":\"ok\",\"payload\":%s}", request_id, payload_json);
   string resp;
   int code = HttpPost(url, body, resp);
   Print("COMPLETE: request_id=", request_id, " code=", code, " resp=", resp);
   if(code != 200) Print("COMPLETE_ERROR: last_error=", GetLastError());
}

void Fail(const string request_id, const string message)
{
   string url = GatewayBaseURL + "/bridge/results";
   string body = StringFormat("{\"request_id\":\"%s\",\"status\":\"error\",\"error\":\"%s\"}", request_id, message);
   Print("DEBUG_FAIL: request_id=[", request_id, "] message=[", message, "] url=[", url, "]");
   Print("DEBUG_FAIL: body=[", body, "]");
   string resp; int __code = HttpPost(url, body, resp); 
   Print("EA_RESULT_POST: code=", __code, " url=", url, " resp=[", resp, "]");
   if(__code != 200) Print("DEBUG_FAIL_ERROR: last_error=", GetLastError());
}

void ProcessCommand(const string cmd)
{
   Print("DEBUG_PROCESSCMD: RAW_CMD=[", cmd, "]");
   if(cmd=="NONE" || cmd=="") {
      Print("DEBUG_PROCESSCMD: cmd is NONE or empty, skipping");
      return;
   }
   Print("DEBUG_PROCESSCMD: About to parse request_id");
   string rid; if(!ParseKV(cmd, "request_id", rid)) {
      Print("DEBUG_PROCESSCMD: Failed to get request_id");
      return;
   }
   Print("DEBUG_PROCESSCMD: request_id=[", rid, "]");
   string type; if(!ParseKV(cmd, "type", type)) { 
      Print("DEBUG_PROCESSCMD: Failed to get type, calling Fail");
      Fail(rid, "missing_type"); 
      return; 
   }
   Print("DEBUG_PROCESSCMD: type=[", type, "]");
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
   } else if(type=="submit_order"){
      string sym; string side; string vols; string sls; string tps; string devs;
      if(!ParseKV(cmd, "symbol", sym) || !ParseKV(cmd, "side", side) || !ParseKV(cmd, "volume_lots", vols)) { Fail(rid, "bad_args"); return; }
      double vol = StringToDouble(vols);
      double sl = 0.0; double tp = 0.0; int dev=20;
      if(ParseKV(cmd, "sl", sls)) sl = StringToDouble(sls);
      if(ParseKV(cmd, "tp", tps)) tp = StringToDouble(tps);
      if(ParseKV(cmd, "deviation", devs)) dev = (int)StringToInteger(devs);
      string side_lower = side;
      StringToLower(side_lower);
      ENUM_ORDER_TYPE ot = (side_lower=="buy" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      MqlTradeRequest req; MqlTradeResult res; ZeroMemory(req); ZeroMemory(res);
      req.action = TRADE_ACTION_DEAL;
      req.symbol = sym;
      req.volume = vol;
      req.type = ot;
      req.type_filling = ORDER_FILLING_FOK;
      req.deviation = dev;
      if(sl>0) req.sl = sl; if(tp>0) req.tp = tp;
      bool ok = OrderSend(req, res);
      string payload = StringFormat("{\"retcode\":%d,\"order\":%I64d,\"deal\":%I64d,\"ask\":%G,\"bid\":%G}", res.retcode, res.order, res.deal, SymbolInfoDouble(sym, SYMBOL_ASK), SymbolInfoDouble(sym, SYMBOL_BID));
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
      bool ok = PositionModifySLTPByTicket(ticket, sl, tp);
      if(ok) Complete(rid, "{\"status\":\"ok\"}"); else Fail(rid, "modify_failed");
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
      double price = StringToDouble(ps); double vol = StringToDouble(vols);
      double sl=0, tp=0; int dev=20; if(ParseKV(cmd, "sl", sls)) sl=StringToDouble(sls); if(ParseKV(cmd, "tp", tps)) tp=StringToDouble(tps); if(ParseKV(cmd, "deviation", devs)) dev=(int)StringToInteger(devs);
       CTrade trade; trade.SetDeviationInPoints(dev); bool ok=false;
      string s = side; StringToLower(s); string k = kind; StringToLower(k);
      if(s=="buy" && k=="limit") ok=trade.BuyLimit(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
      else if(s=="sell" && k=="limit") ok=trade.SellLimit(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
      else if(s=="buy" && k=="stop") ok=trade.BuyStop(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
      else if(s=="sell" && k=="stop") ok=trade.SellStop(vol, price, sym, sl, tp, ORDER_TIME_GTC, 0, "");
      if(ok) Complete(rid, "{\"status\":\"ok\"}"); else Fail(rid, "pending_failed");
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
   } else {
      Fail(rid, "unknown_command");
   }
}
