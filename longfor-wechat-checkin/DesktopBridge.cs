using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public class DesktopBridge {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    delegate bool EnumWindowProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowProc callback, IntPtr data);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder text, int count);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int command);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] static extern bool SwitchDesktop(IntPtr desktop);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public MOUSEINPUT mouse; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx,dy; public uint mouseData,dwFlags,time; public UIntPtr dwExtraInfo; }
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint n, INPUT[] inputs, int size);
    static JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength=4000000 };
    static Dictionary<string,string> args = new Dictionary<string,string>();
    static string Option(string k,string fallback="") { return args.ContainsKey(k)?args[k]:fallback; }
    static bool Unlocked() { IntPtr d=OpenInputDesktop(0,false,0x100); if(d==IntPtr.Zero)return false; try{return SwitchDesktop(d);}finally{CloseDesktop(d);} }
    static string Title(IntPtr h) { var s=new System.Text.StringBuilder(512);GetWindowText(h,s,512);return s.ToString(); }
    static IntPtr FindWindow() {
        var windows=new List<IntPtr>();
        EnumWindows((h,p)=> { uint id;GetWindowThreadProcessId(h,out id);try {
            if(IsWindowVisible(h)&&Title(h)=="龙湖天街"&&Process.GetProcessById((int)id).ProcessName=="WeChatAppEx")windows.Add(h);
        }catch{}return true;},IntPtr.Zero);
        if(windows.Count!=1)throw new Exception("Expected one visible 龙湖天街 WeChatAppEx window; found "+windows.Count);
        return windows[0];
    }
    static void Foreground(IntPtr h) {
        for(int attempt=0;attempt<3;attempt++) {
            if(!IsWindow(h)||!IsWindowVisible(h))throw new Exception("Target window disappeared; refusing input");
            if(GetForegroundWindow()==h)return;
            if(IsIconic(h))ShowWindow(h,9);
            SetForegroundWindow(h);Thread.Sleep(350);
            if(GetForegroundWindow()==h)return;
        }
        throw new Exception("Target window is not foreground; refusing input");
    }
    static List<Dictionary<string,object>> Elements(IntPtr h) {
        var root=AutomationElement.FromHandle(h);
        var found=root.FindAll(TreeScope.Descendants,Condition.TrueCondition);
        var rows=new List<Dictionary<string,object>>();
        for(int i=0;i<found.Count&&i<3000;i++) { try {
            var c=found[i].Current;if(c.IsPassword)continue;
            var r=c.BoundingRectangle;
            rows.Add(new Dictionary<string,object>{{"index",i},{"name",c.Name??""},{"type",c.ControlType.ProgrammaticName},
                {"automation_id",c.AutomationId??""},{"class_name",c.ClassName??""},{"enabled",c.IsEnabled},{"offscreen",c.IsOffscreen},
                {"x",double.IsInfinity(r.X)?0:r.X},{"y",double.IsInfinity(r.Y)?0:r.Y},{"width",double.IsInfinity(r.Width)?0:r.Width},{"height",double.IsInfinity(r.Height)?0:r.Height}});
        }catch{} }
        return rows;
    }
    static bool Safe( List<Dictionary<string,object>> rows ) {
        string[] stops={"验证码","滑块","安全验证","拖动滑块","操作异常","访问异常","请先登录","立即登录","授权登录","手机号登录","短信验证"};
        foreach(var row in rows) {var text=(string)row["name"];foreach(string s in stops)if(text.Contains(s))return false;}
        return true;
    }
    static bool VerificationSafe(List<Dictionary<string,object>> rows) {
        string[] stops={"验证码","滑块","安全验证","拖动滑块","操作异常","访问异常","短信验证"};
        foreach(var row in rows)foreach(string s in stops)if(((string)row["name"]).Contains(s))return false;
        return true;
    }
    static string Digest(string value) {
        using(var sha=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(value))).Replace("-","").ToLowerInvariant();
    }
    static void Click(IntPtr h,int x,int y) {
        if(!Unlocked())throw new Exception("Desktop locked; refusing input");
        Foreground(h);RECT rect;GetWindowRect(h,out rect);
        if(x<=rect.Left||x>=rect.Right||y<=rect.Top||y>=rect.Bottom)throw new Exception("Point outside target window");
        var under=WindowFromPoint(new POINT{X=x,Y=y});
        if(GetAncestor(under,2)!=h)throw new Exception("Click point belongs to another window; refusing input");
        if(!SetCursorPos(x,y))throw new Exception("Unable to place pointer");
        var input=new[]{new INPUT{type=0,mouse=new MOUSEINPUT{dwFlags=0x0002}},new INPUT{type=0,mouse=new MOUSEINPUT{dwFlags=0x0004}}};
        if(SendInput(2,input,Marshal.SizeOf(typeof(INPUT)))!=2)throw new Exception("Mouse input failed; outcome uncertain");
    }
    [STAThread] public static int Main(string[] argv) {
        Console.OutputEncoding=new System.Text.UTF8Encoding(false);SetProcessDPIAware();
        for(int i=0;i<argv.Length;i++)if(argv[i].StartsWith("--")){string key=argv[i],value="true";if(i+1<argv.Length&&!argv[i+1].StartsWith("--"))value=argv[++i];args[key]=value;}
        try {
            if(!Unlocked())throw new Exception("Desktop locked or unavailable; user must unlock it");
            IntPtr h=FindWindow();if(Option("foreground")=="true"||Option("--foreground")=="true")Foreground(h);
            var rows=Elements(h);RECT rect;GetWindowRect(h,out rect);
            string action=Option("--action","inspect");
            if(action=="scroll-member") {
                if(!Safe(rows)||!rows.Exists(r=>(string)r["name"]=="赚珑珠")||!rows.Exists(r=>(string)r["name"]=="我的珑珠"))throw new Exception("Verified member page is required for scrolling");
                Foreground(h);GetWindowRect(h,out rect);
                int x=rect.Left+(rect.Right-rect.Left)/2,y=rect.Top+(rect.Bottom-rect.Top)/2;
                if(GetAncestor(WindowFromPoint(new POINT{X=x,Y=y}),2)!=h)throw new Exception("Scroll point is covered by another window");
                string direction=Option("--direction");if(direction!="up"&&direction!="down")throw new Exception("Invalid scroll direction");
                if(!SetCursorPos(x,y))throw new Exception("Unable to place pointer");
                var wheel=new[]{new INPUT{type=0,mouse=new MOUSEINPUT{dwFlags=0x0800,mouseData=unchecked((uint)(direction=="up"?240:-240))}}};
                if(SendInput(1,wheel,Marshal.SizeOf(typeof(INPUT)))!=1)throw new Exception("Scroll failed");
                Console.WriteLine(serializer.Serialize(new {ok=true,action=action}));return 0;
            }
            if(action=="click-name"||action=="click-id"||action=="click-class"||action=="close-applet"||action=="login-click") {
                bool loggingIn=action=="login-click";
                if(loggingIn) {
                    string loginName=Option("--name");
                    bool entry=loginName=="立即登录"&&rows.Exists(r=>(string)r["name"]=="登录查看成长值及会员等级");
                    bool member=loginName=="会员"&&rows.Exists(r=>(string)r["name"]=="首页");
                    bool quick=loginName=="快捷登录"&&rows.Exists(r=>(string)r["name"]=="欢迎登录龙湖")&&rows.Exists(r=>(string)r["name"]=="我已阅读并同意");
                    bool agree=loginName=="同意"&&Option("--consent")=="true"&&rows.Exists(r=>(string)r["name"]=="服务协议及隐私保护")&&rows.Exists(r=>(string)r["name"]=="欢迎登录龙湖");
                    bool phone=System.Text.RegularExpressions.Regex.IsMatch(loginName,@"^1\d{2}\*{4}\d{4}$")&&Digest(loginName)==Option("--phone-hash")&&rows.Exists(r=>(string)r["name"]=="龙湖｜善待你一生")&&rows.Exists(r=>(string)r["name"]=="申请注册并验证你的手机号")&&rows.Exists(r=>(string)r["name"]=="上次登录");
                    if(!VerificationSafe(rows)||!(entry||member||quick||agree||phone))throw new Exception("Unverified login entry or verification detected; refusing click");
                } else if(!Safe(rows))throw new Exception("Login, verification or abnormal state detected; refusing click");
                bool closing=action=="close-applet";
                string name=closing?"capsule_icon right_icon":action=="click-id"?Option("--id"):action=="click-class"?Option("--class"):Option("--name"),required=Option("--require");
                if(name.Length==0)throw new Exception("Missing exact element name");
                if(required.Length>0&&!rows.Exists(r=>(string)r["name"]==required))throw new Exception("Required page marker is absent");
                var matches=rows.FindAll(r=>(string)r[action=="click-id"?"automation_id":(action=="click-class"||closing)?"class_name":"name"]==name&&(bool)r["enabled"]&&!(bool)r["offscreen"]&&(double)r["width"]>0&&(double)r["height"]>0);
                if(matches.Count!=1)throw new Exception("Expected one clickable exact name; found "+matches.Count);
                var e=matches[0];double fx=double.Parse(Option("--fx","0.5"),System.Globalization.CultureInfo.InvariantCulture),fy=double.Parse(Option("--fy","0.5"),System.Globalization.CultureInfo.InvariantCulture);
                if(fx<=0||fx>=1||fy<=0||fy>=1)throw new Exception("Invalid within-element fractions");
                if(closing&&((double)e["x"]<rect.Left+(rect.Right-rect.Left)*0.65||(double)e["y"]>rect.Top+(rect.Bottom-rect.Top)*0.15))throw new Exception("Close icon is not in the verified upper-right area");
                Click(h,(int)((double)e["x"]+(double)e["width"]*fx),(int)((double)e["y"]+(double)e["height"]*fy));
                if(closing) {
                    for(int i=0;i<80;i++) {
                        if(!IsWindow(h)||!IsWindowVisible(h)) {Console.WriteLine(serializer.Serialize(new {ok=true,action=action,closed=true}));return 0;}
                        Thread.Sleep(100);
                    }
                    throw new Exception("Close icon clicked but applet window remains visible; no repeat click");
                }
                Console.WriteLine(serializer.Serialize(new {ok=true,action=action,name=name}));return 0;
            }
            if(action!="inspect")throw new Exception("Unknown action");
            string capture=Option("--capture");
            if(capture.Length>0) {
                Foreground(h);GetWindowRect(h,out rect);int width=rect.Right-rect.Left,height=rect.Bottom-rect.Top;
                if(width<=0||height<=0||width>4000||height>4000)throw new Exception("Invalid capture bounds");
                using(var bitmap=new Bitmap(width,height)) {using(var g=Graphics.FromImage(bitmap))g.CopyFromScreen(rect.Left,rect.Top,0,0,new Size(width,height));bitmap.Save(capture,ImageFormat.Png);}
                rows=Elements(h);
            }
            Console.WriteLine(serializer.Serialize(new {ok=true,window=new {handle=h.ToInt64(),title=Title(h),x=rect.Left,y=rect.Top,width=rect.Right-rect.Left,height=rect.Bottom-rect.Top},elements=rows,capture=capture}));return 0;
        }catch(Exception e){Console.WriteLine(serializer.Serialize(new{ok=false,error=e.Message}));return 2;}
    }
}
