// Orion AI Assistant - product launcher.
// Its only job is to give Orion a real name and icon in Windows (desktop, Startup apps list):
// it starts the hidden PowerShell entry that sits next to it and exits at once. No console, no wait.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

internal static class Orion
{
    private static readonly string[] Allowed = { "-Startup", "-Background", "-Stop", "-Repair", "-NonInteractive" };

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);

    [STAThread]
    private static int Main(string[] args)
    {
        string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string script = Path.Combine(folder, "Orion.ps1");
        string shell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            @"System32\WindowsPowerShell\v1.0\powershell.exe");
        if (!File.Exists(script) || !File.Exists(shell))
        {
            MessageBoxW(IntPtr.Zero, "Orion launcher files are missing. Reinstall or repair Orion.", "Orion AI Assistant", 0x10);
            return 2;
        }
        var line = new StringBuilder("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"")
            .Append(script).Append('"');
        foreach (string arg in args)  // Only the known switches are forwarded; anything else is dropped.
        {
            foreach (string known in Allowed)
            {
                if (string.Equals(arg, known, StringComparison.OrdinalIgnoreCase)) { line.Append(' ').Append(known); break; }
            }
        }
        var start = new ProcessStartInfo(shell, line.ToString())
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            WorkingDirectory = folder
        };
        try { Process.Start(start).Dispose(); }
        catch (Exception) { return 3; }
        return 0;
    }
}
