# Folder picker for the setup window: Windows' own "Select folder" dialog (the Explorer-style one, not the old
# tree), owned by a topmost window so it opens in front of the setup window. Prints the chosen path, or nothing.
# The start folder comes in ORION_PICK_START (never on the command line). Kept ASCII for Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class OrionFolderPicker {
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    class FileOpenDialog {}

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
        void BindToHandler(IntPtr bc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    }

    // IFileDialog in vtable order, up to GetResult (IModalWindow.Show first).
    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileDialog {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes(uint count, IntPtr types);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr bc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
                                                   out IShellItem item);

    const uint PickFolders = 0x20, ForceFileSystem = 0x40, PathMustExist = 0x800;
    const uint FileSystemPath = 0x80058000;

    public static string Pick(IntPtr owner, string title, string start) {
        IFileDialog dialog = (IFileDialog)new FileOpenDialog();
        try {
            dialog.SetOptions(PickFolders | ForceFileSystem | PathMustExist);
            dialog.SetTitle(title);
            if (!String.IsNullOrEmpty(start)) {
                try {
                    IShellItem folder;
                    SHCreateItemFromParsingName(start, IntPtr.Zero, typeof(IShellItem).GUID, out folder);
                    dialog.SetFolder(folder);
                } catch (Exception) {}  // A folder that does not exist yet: the dialog's own default.
            }
            if (dialog.Show(owner) != 0) return null;  // Cancelled.
            IShellItem result;
            dialog.GetResult(out result);
            string path;
            result.GetDisplayName(FileSystemPath, out path);
            return path;
        } finally {
            Marshal.ReleaseComObject(dialog);
        }
    }
}
'@
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Show()
$owner.Activate()
try {
    $title = [regex]::Unescape('Orion nereye kurulsun?')
    $chosen = [OrionFolderPicker]::Pick($owner.Handle, $title, $env:ORION_PICK_START)
    if ($chosen) { [Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::Out.Write($chosen) }
} finally {
    $owner.Close()
}
