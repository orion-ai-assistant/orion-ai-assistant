// Orion AI Assistant - product launcher and the one long-lived Orion process of an installation.
//
// Started by the desktop/Start/Startup entries (and by Orion.ps1 when it is run directly), it stays running as
// the installation's host ("Orion Router" for a Router-only installation):
//   - Task Manager shows one "Orion AI Assistant" entry with the Orion icon and every Orion process under it:
//     the services' supervisors and the update watcher are created as this process's children (Python's
//     services.shared.orion_host, by whoever starts them) and so are their services and databases.
//   - It runs inside a kill-on-close job whose only handle it holds; the supervisors inherit the job and their
//     own jobs nest inside it. However this process ends ("End task", a crash, Windows signing out) Windows
//     ends every Orion process with it.
//   - It is the notification-area icon: open (the title or a click), quit (the launcher's normal stop), and Windows
//     sign-out (asks every supervisor to stop through its stop request file, without starting a process).
// The work itself stays in Orion.ps1 / the Python launcher, which it runs hidden. A second start hands its
// request to the running host (WM_COPYDATA to its hidden window) and exits. Without a readable installation
// record, elevated, for Repair or a non-interactive run it only starts Orion.ps1 and exits, as it always did.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class Orion
{
    private static readonly string[] Allowed = { "-Startup", "-Background", "-Stop", "-Repair", "-NonInteractive" };
    internal const string WindowPrefix = "Orion-Host-";      // + installation id: the host's hidden window.
    internal const string JobPrefix = @"Local\Orion-HostJob-"; // + installation id: its kill-on-close job.
    internal const int Request = 0x4F52494F;                  // WM_COPYDATA tag of a forwarded start/stop.
    internal const string UiPrefix = "Orion-Ui-";             // + installation id: Orion's window (OrionUi.exe).
    internal const int UiCommand = 0x4F52554F;                // WM_COPYDATA tag of a command to that window.
    internal const int UiState = 0x4F525553;                  // WM_COPYDATA tag of its state ("listening=1").

    [STAThread]
    private static int Main(string[] args)
    {
        string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string script = Path.Combine(folder, "Orion.ps1");
        if (!File.Exists(script) || !File.Exists(Native.PowerShell))
        {
            Native.MessageBoxW(IntPtr.Zero, "Orion launcher files are missing. Reinstall or repair Orion.", Product(), 0x10);
            return 2;
        }
        var switches = new List<string>();
        foreach (string arg in args)  // Only the known switches are used; anything else is dropped.
        {
            foreach (string known in Allowed)
            {
                if (string.Equals(arg, known, StringComparison.OrdinalIgnoreCase) && !switches.Contains(known)) switches.Add(known);
            }
        }
        string registry = Path.Combine(Path.GetDirectoryName(folder), "install.json");
        Dictionary<string, object> record = Installation.Read(registry);
        string id = record == null ? null : record["installation_id"] as string;
        if (id == null || !Regex.IsMatch(id, "^[A-Za-z0-9-]{1,64}$") || switches.Contains("-Repair")
            || switches.Contains("-NonInteractive") || Elevated())
        {
            // Orion.ps1 handles these on its own (an elevated start re-opens Orion with normal rights).
            return Native.Run(Entry(script, switches), folder, false) == IntPtr.Zero ? 3 : 0;
        }
        if (Forward(id, switches)) return 0;
        if (switches.Contains("-Stop"))
        {
            return Native.Run(Entry(script, switches), folder, false) == IntPtr.Zero ? 3 : 0;  // No host: nothing hosted.
        }
        if (switches.Contains("-Startup") && !Host.StartWithWindows(record)) return 0;  // Turned off: nothing starts.
        bool created;
        using (var mutex = new Mutex(true, @"Local\Orion-Host-" + id, out created))
        {
            if (!created)
            {
                // Another host is starting right now: give it the request once its window exists.
                for (int i = 0; i < 100 && !Forward(id, switches); i++) Thread.Sleep(100);
                return 0;
            }
            IntPtr job = Native.CreateHostJob(JobPrefix + id);
            if (job == IntPtr.Zero)
            {
                // Without its job a host could not end its processes: start Orion the way it always started.
                return Native.Run(Entry(script, switches), folder, false) == IntPtr.Zero ? 3 : 0;
            }
            Environment.SetEnvironmentVariable("ORION_HOST_PID", Process.GetCurrentProcess().Id.ToString());
            Environment.SetEnvironmentVariable("ORION_INSTALLATION_REGISTRY", registry);
            Native.DpiAware();
            Application.EnableVisualStyles();
            using (var host = new Host(id, folder, script, registry))
            {
                host.Begin(switches);
                Application.Run();
            }
            mutex.ReleaseMutex();
            // The job handle is closed by Windows as this process ends: everything still in the job ends with it.
        }
        return 0;
    }

    internal static string Product()
    {
        var attributes = Assembly.GetExecutingAssembly().GetCustomAttributes(typeof(AssemblyProductAttribute), false);
        return attributes.Length > 0 ? ((AssemblyProductAttribute)attributes[0]).Product : "Orion AI Assistant";
    }

    internal static string Entry(string script, IEnumerable<string> switches)
    {
        var line = new StringBuilder("\"").Append(Native.PowerShell)
            .Append("\" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"").Append(script).Append('"');
        foreach (string item in switches) line.Append(' ').Append(item);
        return line.ToString();
    }

    private static bool Elevated()
    {
        try { return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); }
        catch (Exception) { return false; }
    }

    // Hands the request to this installation's running host. False when there is none.
    private static bool Forward(string id, List<string> switches)
    {
        IntPtr window = Native.FindWindowW(null, WindowPrefix + id);
        if (window == IntPtr.Zero) return false;
        string text = string.Join(" ", switches.ToArray());
        IntPtr data = Marshal.StringToHGlobalUni(text);
        try
        {
            var message = new Native.CopyData { Tag = (IntPtr)Request, Size = (text.Length + 1) * 2, Data = data };
            IntPtr answer;
            return Native.SendMessageTimeoutW(window, Native.WM_COPYDATA, IntPtr.Zero, ref message, 0x2, 10000, out answer) != IntPtr.Zero
                && answer == (IntPtr)1;
        }
        finally { Marshal.FreeHGlobal(data); }
    }
}

// The installation record and the supervisors' state files (read only; written by the Python side).
internal static class Installation
{
    internal static Dictionary<string, object> Read(string path)
    {
        try { return new JavaScriptSerializer().DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>; }
        catch (Exception) { return null; }
    }

    // State folders of the supervisors this installation runs (services.shared.process_lifecycle: <state>/<mode>).
    internal static List<string> SupervisorFolders(string registry)
    {
        var folders = new List<string>();
        var record = Read(registry);
        if (record == null || !(record.ContainsKey("components") && record["components"] is IEnumerable)) return folders;
        foreach (object item in (IEnumerable)record["components"])
        {
            var component = item as Dictionary<string, object>;
            if (component == null) continue;
            string kind = Text(component, "kind"), state = Text(component, "state_path");
            string mode = kind == "hub" ? "hub" : kind == "router" ? "router-prod" : null;
            if (mode == null || Text(component, "management") == "connect-only" || string.IsNullOrEmpty(state)) continue;
            try { folders.Add(Path.Combine(state, mode)); } catch (ArgumentException) { }
        }
        return folders;
    }

    // processes.json of a running supervisor: (status, run_id); null when none is running there.
    internal static Dictionary<string, object> Running(string folder)
    {
        var state = Read(Path.Combine(folder, "processes.json"));
        string status = state == null ? null : Text(state, "status");
        return status == null || status == "stopped" || status == "failed" ? null : state;
    }

    // The installed Router panel's favicon.ico (every size, orion-router dashboard/public); null without a local Router.
    internal static string RouterFavicon(string registry)
    {
        var record = Read(registry);
        if (record == null || !(record.ContainsKey("components") && record["components"] is IEnumerable)) return null;
        foreach (object item in (IEnumerable)record["components"])
        {
            var component = item as Dictionary<string, object>;
            if (component == null) continue;
            string code = Text(component, "code_path");
            if (Text(component, "kind") != "router" || Text(component, "management") == "connect-only" || string.IsNullOrEmpty(code)) continue;
            try { return Path.Combine(code, @"dashboard\out\favicon.ico"); } catch (ArgumentException) { }
        }
        return null;
    }

    internal static string Text(Dictionary<string, object> map, string key)
    {
        object value;
        return map.TryGetValue(key, out value) ? value as string : null;
    }
}

internal sealed class Host : Form
{
    private readonly string id, folder, script, registry, exitMarker;
    private readonly NotifyIcon icon = new NotifyIcon();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    private readonly List<IntPtr> starts = new List<IntPtr>();  // Orion.ps1 runs this host started (open/start).
    private IntPtr stopping = IntPtr.Zero;                        // The Orion.ps1 -Stop run of "Quit".
    private bool exitRequested, reopen, ending, closed;
    private DateTime lastOpen = DateTime.MinValue;
    private string routerFavicon;                                 // The file routerBig/routerSmall are from.
    private IntPtr routerBig, routerSmall;
    private ToolStripMenuItem stopListening;                       // Shown while Orion's window listens.
    private string iconText = "Orion";

    internal Host(string id, string folder, string script, string registry)
    {
        this.id = id; this.folder = folder; this.script = script; this.registry = registry;
        exitMarker = Path.Combine(folder, "host.exit");
        Text = Orion.WindowPrefix + id;  // How launchers and Python find this host.
        ShowInTaskbar = false; FormBorderStyle = FormBorderStyle.None; Opacity = 0;
        StartPosition = FormStartPosition.Manual; Size = new Size(1, 1); Location = new Point(-32000, -32000);
        try { File.Delete(exitMarker); } catch (Exception) { }  // A previous host's unanswered request.
    }

    internal void Begin(List<string> switches)
    {
        Show(); Hide();  // A real (hidden) top-level window: Windows asks it before signing out.
        BuildIcon();
        timer.Interval = 500;
        timer.Tick += delegate { Tick(); };
        timer.Start();
        Open(switches, true);
    }

    internal static bool StartWithWindows(Dictionary<string, object> record)
    {
        object preferences, value;
        if (record != null && record.TryGetValue("preferences", out preferences) && preferences is Dictionary<string, object>
            && ((Dictionary<string, object>)preferences).TryGetValue("start_with_windows", out value) && value is bool)
        {
            return (bool)value;
        }
        return true;
    }

    private void BuildIcon()
    {
        Ui.Load();
        icon.Icon = Ui.OrionIcon(SystemInformation.SmallIconSize.Width);
        iconText = Orion.Product() == "Orion Router" ? "Orion Router" : "Orion";
        icon.Text = iconText;
        var menu = new ContextMenuStrip();
        Ui.Style(menu);
        var title = new ToolStripMenuItem(Orion.Product());
        var image = Ui.OrionIcon(Ui.Scale(20));
        title.Image = image.ToBitmap();
        title.Font = Ui.Font(10, FontStyle.Bold);
        title.ForeColor = Ui.Accent;
        title.Click += delegate { Open(new List<string>(), false); };
        menu.Items.Add(title);
        menu.Items.Add(new ToolStripSeparator());
        // The title opens Orion, Quit stops it (uninstall: Windows Settings > Apps). While Orion's window
        // (OrionUi.exe, also hidden) listens, "Dinlemeyi durdur" ends that listening.
        stopListening = new ToolStripMenuItem("Dinlemeyi durdur");
        stopListening.ForeColor = Ui.Foreground; stopListening.Padding = new Padding(0, 3, 0, 3);
        stopListening.Visible = false;
        stopListening.Click += delegate { UiCommand("stop-listening", false); };
        menu.Items.Add(stopListening);
        var quit = new ToolStripMenuItem("\u00c7\u0131k");
        menu.Items.Add(quit);
        quit.ForeColor = Ui.Foreground; quit.Padding = new Padding(0, 3, 0, 3);
        quit.Click += delegate { Quit(); };
        icon.ContextMenuStrip = menu;
        icon.MouseClick += (sender, e) => { if (e.Button == MouseButtons.Left) Open(new List<string>(), false); };
        icon.Visible = true;
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == Native.WM_COPYDATA)
        {
            var data = (Native.CopyData)Marshal.PtrToStructure(message.LParam, typeof(Native.CopyData));
            if (data.Tag == (IntPtr)Orion.Request && data.Size >= 2 && data.Size <= 512)
            {
                var switches = new List<string>(Marshal.PtrToStringUni(data.Data, data.Size / 2 - 1)
                    .Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries));
                message.Result = (IntPtr)1;
                BeginInvoke((MethodInvoker)delegate
                {
                    if (switches.Contains("-Stop")) Quit(); else Open(switches, false);
                });
                return;
            }
            if (data.Tag == (IntPtr)Orion.UiState && data.Size >= 2 && data.Size <= 512)
            {
                // Orion's window reports whether the assistant listens (it may be hidden).
                bool value = Marshal.PtrToStringUni(data.Data, data.Size / 2).TrimEnd('\0') == "listening=1";
                message.Result = (IntPtr)1;
                BeginInvoke((MethodInvoker)delegate { Listening(value); });
                return;
            }
        }
        base.WndProc(ref message);
    }

    // Start (or show) Orion: the launcher starts what is not running and opens the window.
    private void Open(List<string> switches, bool first)
    {
        if (closed) return;
        if (stopping != IntPtr.Zero) { reopen = true; return; }  // After the stop that is running now.
        if (!first && !switches.Contains("-Background"))
        {
            if ((DateTime.Now - lastOpen).TotalSeconds < 3) return;  // Ignore double clicks: one launch at a time.
            lastOpen = DateTime.Now;
        }
        if (!first && !switches.Contains("-Background") && UiCommand("show", true))
        {
            // Orion's window runs (hidden): it is shown at once with its page as it was. The launcher still makes
            // sure the services run, without a window of its own.
            IntPtr check = Native.Run(Orion.Entry(script, new List<string> { "-Background" }), folder, false);
            if (check != IntPtr.Zero) starts.Add(check);
            return;
        }
        var allowed = new List<string>();
        foreach (string item in switches) if (item == "-Startup" || item == "-Background") allowed.Add(item);
        IntPtr process = Native.Run(Orion.Entry(script, allowed), folder, false);
        if (process != IntPtr.Zero) starts.Add(process);
        else if (first) Exit();
    }

    // Orion's window (OrionUi.exe): a message-only window named after this installation takes commands.
    private bool UiCommand(string command, bool front)
    {
        IntPtr window = Native.FindWindowExW(Native.HWND_MESSAGE, IntPtr.Zero, null, Orion.UiPrefix + id);
        if (window == IntPtr.Zero) return false;
        if (front)
        {
            int pid;
            Native.GetWindowThreadProcessId(window, out pid);
            Native.AllowSetForegroundWindow(pid);  // The user's click is ours: hand the foreground on.
        }
        byte[] bytes = Encoding.Unicode.GetBytes(command + "\0");
        IntPtr buffer = Marshal.AllocHGlobal(bytes.Length);
        try
        {
            Marshal.Copy(bytes, 0, buffer, bytes.Length);
            var data = new Native.CopyData { Tag = (IntPtr)Orion.UiCommand, Size = bytes.Length, Data = buffer };
            IntPtr result;
            return Native.SendMessageTimeoutW(window, Native.WM_COPYDATA, IntPtr.Zero, ref data, 0x2, 5000, out result) != IntPtr.Zero
                && result != IntPtr.Zero;
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    private void Listening(bool value)
    {
        if (closed) return;
        stopListening.Visible = value;
        icon.Text = value ? iconText + " - dinliyor" : iconText;
    }

    // "Quit": the launcher's normal stop (one shutdown path); this host ends when it has finished.
    private void Quit()
    {
        if (closed || stopping != IntPtr.Zero) return;
        reopen = false;
        icon.Visible = false;
        stopping = Native.Run(Orion.Entry(script, new[] { "-Stop" }), folder, false);
        if (stopping == IntPtr.Zero) icon.Visible = true;
    }

    private void Tick()
    {
        if (closed) return;
        RouterWindowIcon();
        if (File.Exists(exitMarker))
        {
            // The launcher stopped every service (also one that was not started from here): end.
            try { File.Delete(exitMarker); } catch (Exception) { }
            exitRequested = true;
            icon.Visible = false;
        }
        if (!File.Exists(registry)) exitRequested = true;  // Uninstalled.
        bool finished = false;
        for (int i = starts.Count - 1; i >= 0; i--)
        {
            int code;
            if (Native.Finished(starts[i], out code)) { Native.CloseHandle(starts[i]); starts.RemoveAt(i); finished = true; }
        }
        bool startsDone = starts.Count == 0;
        if (stopping != IntPtr.Zero)
        {
            int code;
            if (!Native.Finished(stopping, out code)) return;
            Native.CloseHandle(stopping);
            stopping = IntPtr.Zero;
            if (code != 0 && AnyRunning() && !exitRequested)
            {
                icon.Visible = true;  // Orion.ps1 explained what still runs; Quit can be tried again.
                return;
            }
            if (reopen && File.Exists(registry))
            {
                // Opened again while it was closing: start again instead of ending.
                reopen = false; exitRequested = false;
                icon.Visible = true;
                Open(new List<string>(), true);
                return;
            }
            Exit();
            return;
        }
        if (exitRequested && startsDone) { Exit(); return; }
        // A start that left nothing running (disabled at sign-in, a failure Orion.ps1 explained, a Router-only
        // window without services): no idle host. Asked only then: while an update replaces the services
        // nothing runs either, and the update watcher inside this host's job must keep running.
        if (finished && startsDone && !AnyRunning()) Exit();
    }

    // The Router panel's window (Edge --app, services.shared.flutter_launcher) has the page's 16 px favicon as its
    // only icon, and Windows stretches it to the taskbar's 32 px or more: blurred. The window gets the installed
    // panel's favicon.ico at the sizes Windows asks for, again whenever Edge sets its own (a page load).
    private void RouterWindowIcon()
    {
        IntPtr window = IntPtr.Zero, current;
        while ((window = Native.FindWindowExW(IntPtr.Zero, window, "Chrome_WidgetWin_1", "Orion Router")) != IntPtr.Zero)
        {
            if (Native.SendMessageTimeoutW(window, Native.WM_GETICON, (IntPtr)1, IntPtr.Zero, 0x2, 200, out current) == IntPtr.Zero
                || (current == routerBig && current != IntPtr.Zero)) continue;
            string file = Installation.RouterFavicon(registry);
            if (file != routerFavicon)
            {
                // A Router update brings its own file; the icons of the previous one are kept (a window may show them).
                routerFavicon = file;
                routerBig = file == null ? IntPtr.Zero : Native.ExtractIcon(file, SystemInformation.IconSize.Width);
                routerSmall = file == null ? IntPtr.Zero : Native.ExtractIcon(file, SystemInformation.SmallIconSize.Width);
            }
            if (routerBig == IntPtr.Zero || routerSmall == IntPtr.Zero) return;  // A Router without the file: Edge's icon stays.
            Native.SendMessageTimeoutW(window, Native.WM_SETICON, (IntPtr)1, routerBig, 0x2, 200, out current);
            Native.SendMessageTimeoutW(window, Native.WM_SETICON, (IntPtr)0, routerSmall, 0x2, 200, out current);
        }
    }

    private bool AnyRunning()
    {
        foreach (string state in Installation.SupervisorFolders(registry))
        {
            if (Installation.Running(state) != null) return true;
        }
        return false;
    }

    // Windows sign-out / shutdown. Without this, Windows ended PostgreSQL and the Hub abruptly: the next start ran
    // crash recovery. Windows sends WM_QUERYENDSESSION (FormClosing with WindowsShutDown) and waits, showing
    // "Orion kapat\u0131l\u0131yor", while the services stop. No process may be started now - Windows refuses them while it
    // signs out (0xc0000142) - so every running supervisor of this installation is asked directly, with the stop
    // request each of them already watches (process_lifecycle: stop-<run_id> in its state folder). They stop their
    // services and their database themselves; whatever is left ends with this host's job.
    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        base.OnFormClosing(e);
        if (e.CloseReason != CloseReason.WindowsShutDown || ending) return;
        ending = true;
        try { Native.ShutdownBlockReasonCreate(Handle, "Orion kapat\u0131l\u0131yor..."); } catch (Exception) { }
        icon.Visible = false;
        var folders = new List<string>();
        foreach (string path in Installation.SupervisorFolders(registry))
        {
            var state = Installation.Running(path);
            string run = state == null ? null : Installation.Text(state, "run_id");
            if (run == null || !Regex.IsMatch(run, "^[0-9a-fA-F-]{36}$")) continue;
            try { File.WriteAllBytes(Path.Combine(path, "stop-" + run), new byte[0]); folders.Add(path); }
            catch (Exception) { }
        }
        // A normal stop takes a few seconds; Windows is never held longer than 25 s.
        var deadline = DateTime.Now.AddSeconds(25);
        while (folders.Count > 0 && DateTime.Now < deadline)
        {
            folders.RemoveAll(path => Installation.Running(path) == null);
            if (folders.Count > 0) Thread.Sleep(200);
        }
        try { Native.ShutdownBlockReasonDestroy(Handle); } catch (Exception) { }
        BeginInvoke((MethodInvoker)Exit);
    }

    private void Exit()
    {
        if (closed) return;
        closed = true;
        timer.Stop();
        icon.Visible = false;  // Hide first, or Windows keeps a ghost icon until the next hover.
        icon.Dispose();
        Application.ExitThread();
    }
}

// Orion's look (services/shared/orion_ui.ps1): dark menu, Orion icon, Segoe UI.
internal static class Ui
{
    internal static readonly Color Surface = Color.FromArgb(23, 29, 51);
    internal static readonly Color Foreground = Color.FromArgb(232, 236, 248);
    internal static readonly Color Accent = Color.FromArgb(56, 214, 245);
    private static double scale = 1.0;
    private static string fontName = "Segoe UI";

    internal static void Load()
    {
        try { using (var graphics = Graphics.FromHwnd(IntPtr.Zero)) scale = Math.Max(1.0, graphics.DpiX / 96.0); } catch (Exception) { }
        try
        {
            using (var fonts = new System.Drawing.Text.InstalledFontCollection())
                foreach (var family in fonts.Families) if (family.Name == "Segoe UI Variable Text") fontName = family.Name;
        }
        catch (Exception) { }
    }

    internal static int Scale(double value) { return (int)Math.Round(value * scale); }

    internal static Font Font(float size, FontStyle style)
    {
        try { return new Font(fontName, size, style); } catch (Exception) { return new Font("Segoe UI", size, style); }
    }

    // The icon embedded in this exe (16..256 px) at the size asked for.
    internal static Icon OrionIcon(int size)
    {
        string file = Assembly.GetExecutingAssembly().Location;
        IntPtr handle = Native.ExtractIcon(file, size);
        if (handle != IntPtr.Zero)
        {
            var icon = (Icon)System.Drawing.Icon.FromHandle(handle).Clone();
            Native.DestroyIcon(handle);
            return icon;
        }
        try { return System.Drawing.Icon.ExtractAssociatedIcon(file); } catch (Exception) { return SystemIcons.Application; }
    }

    internal static void Style(ContextMenuStrip menu)
    {
        menu.Renderer = new ToolStripProfessionalRenderer(new MenuColors());
        menu.BackColor = Surface;
        menu.ForeColor = Foreground;
        menu.Font = Font(10, FontStyle.Regular);
        menu.ShowImageMargin = true;
        menu.Padding = new Padding(2, 6, 2, 6);
    }

    private sealed class MenuColors : ProfessionalColorTable
    {
        private static readonly Color Back = Color.FromArgb(23, 29, 51);
        private static readonly Color Hover = Color.FromArgb(38, 48, 82);
        private static readonly Color Line = Color.FromArgb(42, 51, 82);
        public override Color ToolStripDropDownBackground { get { return Back; } }
        public override Color ImageMarginGradientBegin { get { return Back; } }
        public override Color ImageMarginGradientMiddle { get { return Back; } }
        public override Color ImageMarginGradientEnd { get { return Back; } }
        public override Color MenuBorder { get { return Line; } }
        public override Color MenuItemBorder { get { return Hover; } }
        public override Color MenuItemSelected { get { return Hover; } }
        public override Color MenuItemSelectedGradientBegin { get { return Hover; } }
        public override Color MenuItemSelectedGradientEnd { get { return Hover; } }
        public override Color SeparatorDark { get { return Line; } }
        public override Color SeparatorLight { get { return Line; } }
    }
}

internal static class Native
{
    internal const int WM_COPYDATA = 0x004A, WM_GETICON = 0x007F, WM_SETICON = 0x0080;
    internal static readonly IntPtr HWND_MESSAGE = new IntPtr(-3);
    internal static readonly string PowerShell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
        @"System32\WindowsPowerShell\v1.0\powershell.exe");

    [StructLayout(LayoutKind.Sequential)]
    internal struct CopyData { public IntPtr Tag; public int Size; public IntPtr Data; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo
    {
        public int Size; public string Reserved, Desktop, Title;
        public int X, Y, Width, Height, CountChars, CountLines, FillAttribute, Flags;
        public short ShowWindow, Reserved2; public IntPtr Reserved3, Input, Output, Error;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInformation { public IntPtr Process, Thread; public int ProcessId, ThreadId; }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public long PerProcessTime, PerJobTime; public int Flags; public UIntPtr MinWorkingSet, MaxWorkingSet;
        public int ActiveProcesses; public UIntPtr Affinity; public int Priority, Scheduling;
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern IntPtr FindWindowW(string className, string title);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern IntPtr SendMessageTimeoutW(IntPtr window, int message, IntPtr wParam, ref CopyData lParam,
        int flags, int timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern IntPtr SendMessageTimeoutW(IntPtr window, int message, IntPtr wParam, IntPtr lParam,
        int flags, int timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern IntPtr FindWindowExW(IntPtr parent, IntPtr after, string className, string title);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint PrivateExtractIconsW(string file, int index, int cx, int cy, IntPtr[] icons, int[] ids, uint count, uint flags);
    [DllImport("user32.dll")] internal static extern bool DestroyIcon(IntPtr icon);
    [DllImport("user32.dll")] internal static extern int GetWindowThreadProcessId(IntPtr window, out int process);
    [DllImport("user32.dll")] internal static extern bool AllowSetForegroundWindow(int process);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] internal static extern bool ShutdownBlockReasonCreate(IntPtr window, string reason);
    [DllImport("user32.dll")] internal static extern bool ShutdownBlockReasonDestroy(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
        ref StartupInfo startup, out ProcessInformation information);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, int size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll")] private static extern bool GetExitCodeProcess(IntPtr process, out int code);
    [DllImport("kernel32.dll")] internal static extern bool CloseHandle(IntPtr handle);

    // The icon of an .exe or .ico nearest `size` px, which the caller owns; Zero when there is none.
    internal static IntPtr ExtractIcon(string file, int size)
    {
        var handles = new IntPtr[1];
        var ids = new int[1];
        try { return PrivateExtractIconsW(file, 0, size, size, handles, ids, 1, 0) == 1 ? handles[0] : IntPtr.Zero; }
        catch (Exception) { return IntPtr.Zero; }
    }

    internal static void DpiAware()
    {
        try { if (!SetProcessDpiAwarenessContext((IntPtr)(-2))) SetProcessDPIAware(); } catch (Exception) { }
    }

    // A hidden process that inherits no handle. `breakaway`: outside this host's job (it must outlive it).
    internal static IntPtr Run(string commandLine, string directory, bool breakaway)
    {
        var startup = new StartupInfo { Size = Marshal.SizeOf(typeof(StartupInfo)), Flags = 0x1 /* USESHOWWINDOW */ };
        uint flags = 0x08000000;  // CREATE_NO_WINDOW
        if (breakaway) flags |= 0x01000000;  // CREATE_BREAKAWAY_FROM_JOB
        ProcessInformation information;
        if (!CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, false, flags, IntPtr.Zero,
                directory, ref startup, out information)) return IntPtr.Zero;
        CloseHandle(information.Thread);
        return information.Process;
    }

    internal static bool Finished(IntPtr process, out int code)
    {
        code = 0;
        if (WaitForSingleObject(process, 0) != 0) return false;
        GetExitCodeProcess(process, out code);
        return true;
    }

    // The host's job: kill on close (this process holds the only handle), breakaway allowed only for processes
    // started with CREATE_BREAKAWAY_FROM_JOB (an uninstall's cleanup that must outlive Orion). The supervisors' own
    // jobs nest inside it and keep forbidding breakaway. Named, so Python can tell whether it runs inside it.
    internal static IntPtr CreateHostJob(string name)
    {
        IntPtr job = CreateJobObjectW(IntPtr.Zero, name);
        if (job == IntPtr.Zero) return IntPtr.Zero;
        if (Marshal.GetLastWin32Error() == 183) { CloseHandle(job); return IntPtr.Zero; }  // Someone else's.
        var limits = new ExtendedLimits { Flags = 0x2000 | 0x800 };  // KILL_ON_JOB_CLOSE | BREAKAWAY_OK
        if (!SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(typeof(ExtendedLimits)))
            || !AssignProcessToJobObject(job, GetCurrentProcess()))
        {
            CloseHandle(job);
            return IntPtr.Zero;
        }
        return job;
    }
}
