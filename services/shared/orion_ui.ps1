# Orion's look for its small Windows windows (updating, waiting, repair, uninstall, messages, tray menu).
# Dot-sourced by the bootstrap scripts as Orion-Ui.ps1; written next to them by services/shared/desktop_ui.py.
# Dependency-free (Windows PowerShell 5.1 + WinForms) and kept ASCII: Turkish text goes through T with \u escapes.
#   Initialize-OrionUi       once, before the first window: crisp text (DPI), own taskbar identity (not PowerShell)
#   New-OrionForm            dark window with the Orion icon, title bar matching the theme
#   New-OrionHeader          icon + title + subtitle at the top of a window
#   New-OrionLabel/Button/Check/Radio/Progress   themed controls (sizes in 96-DPI units; scaled here)
#   Show-OrionMessage        themed replacement for MessageBox (OK, or Yes/No)
#   Set-OrionMenuStyle       dark tray menu
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
function T([string]$text) { [regex]::Unescape($text) }

if (-not ('OrionUiNative' -as [type])) {
    # Compiled with the .NET Framework that ships with Windows. Without it (blocked compiler) the windows
    # still work, only plainer: every use below checks $script:OrionNative.
    try { Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public static class OrionUiNative
{
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint PrivateExtractIcons(string file, int index, int cx, int cy, IntPtr[] icons, int[] ids, uint count, uint flags);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern bool ShutdownBlockReasonCreate(IntPtr window, string reason);
    [DllImport("user32.dll")] public static extern bool ShutdownBlockReasonDestroy(IntPtr window);

    // One icon of the requested size from an exe (Orion.exe carries 16..256 px); null when unavailable.
    public static Icon ExtractIcon(string file, int size)
    {
        var handles = new IntPtr[1];
        var ids = new int[1];
        try
        {
            if (PrivateExtractIcons(file, 0, size, size, handles, ids, 1, 0) != 1 || handles[0] == IntPtr.Zero) return null;
            var icon = (Icon)Icon.FromHandle(handles[0]).Clone();
            DestroyIcon(handles[0]);
            return icon;
        }
        catch (Exception) { return null; }
    }

    // Title bar in Orion's colours (also when Windows paints title bars in the accent colour) and rounded
    // corners: Windows 11; older Windows ignores the attributes it does not know.
    public static void DarkTitle(IntPtr window, int caption, int text, int border)
    {
        int on = 1, round = 2;
        try { DwmSetWindowAttribute(window, 20, ref on, 4); } catch (Exception) { }
        try { DwmSetWindowAttribute(window, 33, ref round, 4); } catch (Exception) { }
        try { DwmSetWindowAttribute(window, 35, ref caption, 4); } catch (Exception) { }
        try { DwmSetWindowAttribute(window, 36, ref text, 4); } catch (Exception) { }
        try { DwmSetWindowAttribute(window, 34, ref border, 4); } catch (Exception) { }
    }
}

public class OrionMenuColors : ProfessionalColorTable
{
    static readonly Color Back = Color.FromArgb(23, 29, 51);
    static readonly Color Hover = Color.FromArgb(38, 48, 82);
    static readonly Color Line = Color.FromArgb(42, 51, 82);
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
'@ } catch {}
}
$script:OrionNative = [bool]('OrionUiNative' -as [type])

$script:OrionTheme = @{
    Back    = [Drawing.Color]::FromArgb(15, 20, 36)
    Surface = [Drawing.Color]::FromArgb(23, 29, 51)
    Border  = [Drawing.Color]::FromArgb(42, 51, 82)
    Text    = [Drawing.Color]::FromArgb(232, 236, 248)
    Muted   = [Drawing.Color]::FromArgb(154, 165, 196)
    Accent  = [Drawing.Color]::FromArgb(56, 214, 245)
    Accent2 = [Drawing.Color]::FromArgb(164, 107, 245)
    Danger  = [Drawing.Color]::FromArgb(240, 100, 122)
    OnAccent = [Drawing.Color]::FromArgb(10, 14, 28)
}
$script:OrionScale = 1.0
$script:OrionFontName = 'Segoe UI'

function Initialize-OrionUi {
    # System-DPI aware before the first window: text is drawn sharp instead of a stretched 96-DPI bitmap.
    if ($script:OrionNative) {
        try { if (-not [OrionUiNative]::SetProcessDpiAwarenessContext([IntPtr](-2))) { [void][OrionUiNative]::SetProcessDPIAware() } } catch {}
        # Own taskbar / Alt+Tab identity: the window shows the Orion icon instead of being grouped under PowerShell.
        try { [void][OrionUiNative]::SetCurrentProcessExplicitAppUserModelID('Orion.AIAssistant') } catch {}
    }
    try { [Windows.Forms.Application]::EnableVisualStyles() } catch {}
    try {
        $graphics = [Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
        $script:OrionScale = [math]::Max(1.0, $graphics.DpiX / 96.0)
        $graphics.Dispose()
    } catch {}
    $installed = (New-Object Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }
    if ($installed -contains 'Segoe UI Variable Text') { $script:OrionFontName = 'Segoe UI Variable Text' }
}

function ColorRef([Drawing.Color]$color) { [int]$color.R -bor ([int]$color.G -shl 8) -bor ([int]$color.B -shl 16) }
function S([double]$value) { [int][math]::Round($value * $script:OrionScale) }
function Get-OrionFont([double]$size = 10, [Drawing.FontStyle]$style = [Drawing.FontStyle]::Regular, [string]$family) {
    if (-not $family) { $family = $script:OrionFontName }
    try { return New-Object Drawing.Font($family, $size, $style) } catch { return New-Object Drawing.Font('Segoe UI', $size, $style) }
}

function Get-OrionIcon([int]$size = 32) {
    # The icon embedded in the retained Orion.exe (the same file as the desktop/Start/Startup entry).
    $exe = Join-Path $PSScriptRoot 'Orion.exe'
    if (Test-Path -LiteralPath $exe -PathType Leaf) {
        if ($script:OrionNative) {
            $icon = [OrionUiNative]::ExtractIcon($exe, $size)
            if ($icon) { return $icon }
        }
        try { return [Drawing.Icon]::ExtractAssociatedIcon($exe) } catch {}
    }
    return [Drawing.SystemIcons]::Application
}

function New-OrionForm([string]$title, [int]$width, [int]$height) {
    $form = New-Object Windows.Forms.Form
    $form.Text = $title
    $form.AutoScaleMode = 'None'
    $form.ClientSize = New-Object Drawing.Size((S $width), (S $height))
    $form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedSingle'
    $form.MaximizeBox = $false; $form.MinimizeBox = $false
    $form.BackColor = $script:OrionTheme.Back; $form.ForeColor = $script:OrionTheme.Text
    $form.Font = Get-OrionFont 10
    $form.Icon = Get-OrionIcon 32
    $form.ShowInTaskbar = $true
    if ($script:OrionNative) {
        $form.Add_HandleCreated({
            param($sender)
            [OrionUiNative]::DarkTitle($sender.Handle, (ColorRef $script:OrionTheme.Back), (ColorRef $script:OrionTheme.Text), (ColorRef $script:OrionTheme.Border))
        })
    }
    return $form
}

function Add-OrionControl($form, $control, [int]$x, [int]$y, [int]$width, [int]$height) {
    $control.SetBounds((S $x), (S $y), (S $width), (S $height))
    $form.Controls.Add($control)
    return $control
}

function New-OrionHeader($form, [string]$title, [string]$subtitle, [int]$width) {
    # Orion emblem, a title and a muted line under it; returns the title and subtitle labels.
    $picture = New-Object Windows.Forms.PictureBox
    $picture.SizeMode = 'Zoom'
    $picture.Image = (Get-OrionIcon (S 48)).ToBitmap()
    [void](Add-OrionControl $form $picture 24 22 44 44)
    $heading = New-OrionLabel $title 15 ([Drawing.FontStyle]::Bold)
    [void](Add-OrionControl $form $heading 82 20 ($width - 106) 28)
    $sub = New-OrionLabel $subtitle 10 -Muted
    [void](Add-OrionControl $form $sub 82 50 ($width - 106) 22)
    return @($heading, $sub)
}

function New-OrionLabel([string]$text, [double]$size = 10, [Drawing.FontStyle]$style = [Drawing.FontStyle]::Regular, [switch]$Muted) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $text
    $label.Font = Get-OrionFont $size $style
    $label.BackColor = [Drawing.Color]::Transparent
    $label.ForeColor = if ($Muted) { $script:OrionTheme.Muted } else { $script:OrionTheme.Text }
    $label.AutoEllipsis = $true
    return $label
}

function New-OrionButton([string]$text, [switch]$Primary, [switch]$Danger) {
    $button = New-Object Windows.Forms.Button
    $button.Text = $text
    $button.FlatStyle = 'Flat'
    $button.Cursor = [Windows.Forms.Cursors]::Hand
    $button.Font = Get-OrionFont 10 ([Drawing.FontStyle]::Bold)
    $button.UseVisualStyleBackColor = $false
    if ($Primary -or $Danger) {
        $button.BackColor = if ($Danger) { $script:OrionTheme.Danger } else { $script:OrionTheme.Accent }
        $button.ForeColor = $script:OrionTheme.OnAccent
        $button.FlatAppearance.BorderSize = 0
        $button.FlatAppearance.MouseOverBackColor = [Windows.Forms.ControlPaint]::Light($button.BackColor, 0.3)
    } else {
        $button.BackColor = $script:OrionTheme.Surface
        $button.ForeColor = $script:OrionTheme.Text
        $button.FlatAppearance.BorderColor = $script:OrionTheme.Border
        $button.FlatAppearance.MouseOverBackColor = $script:OrionTheme.Border
    }
    return $button
}

function New-OrionCheck([string]$text, [bool]$checked, [switch]$Radio) {
    $box = if ($Radio) { New-Object Windows.Forms.RadioButton } else { New-Object Windows.Forms.CheckBox }
    $box.Text = $text; $box.Checked = $checked
    # Flat glyphs take BackColor for the box and ForeColor for the mark: dark box, light mark, accent border.
    $box.FlatStyle = 'Flat'
    $box.ForeColor = $script:OrionTheme.Text; $box.BackColor = $script:OrionTheme.Back
    $box.FlatAppearance.BorderColor = $script:OrionTheme.Accent
    $box.Cursor = [Windows.Forms.Cursors]::Hand
    return $box
}

function New-OrionProgress {
    # A slim rounded bar in the Orion gradient. Tag.Value: 0..100, or -1 for "working" (a moving segment).
    $bar = New-Object Windows.Forms.Panel
    $bar.BackColor = $script:OrionTheme.Back
    $bar.Tag = @{ Value = -1; Phase = 0.0 }
    $flags = [Reflection.BindingFlags]'Instance, NonPublic'
    [Windows.Forms.Control].GetProperty('DoubleBuffered', $flags).SetValue($bar, $true, $null)
    $bar.Add_Paint({
        param($sender, $e)
        $g = $e.Graphics
        $g.SmoothingMode = 'AntiAlias'
        $w = $sender.ClientSize.Width; $h = $sender.ClientSize.Height
        $track = New-Object Drawing.SolidBrush($script:OrionTheme.Surface)
        $g.FillRectangle($track, 0, 0, $w, $h); $track.Dispose()
        $value = $sender.Tag.Value
        if ($value -ge 0) { $x = 0; $fill = [int]($w * [math]::Min(100, $value) / 100) }
        else { $fill = [int]($w * 0.3); $x = [int](($w + $fill) * $sender.Tag.Phase) - $fill }
        if ($fill -gt 0) {
            $rect = New-Object Drawing.Rectangle(0, 0, [math]::Max(1, $w), $h)
            $brush = New-Object Drawing.Drawing2D.LinearGradientBrush($rect, $script:OrionTheme.Accent2, $script:OrionTheme.Accent, 0.0)
            $g.FillRectangle($brush, $x, 0, $fill, $h); $brush.Dispose()
        }
    })
    $timer = New-Object Windows.Forms.Timer
    $timer.Interval = 30
    $timer.Tag = $bar
    $timer.Add_Tick({ param($sender) $b = $sender.Tag; if ($b.Tag.Value -lt 0) { $b.Tag.Phase = ($b.Tag.Phase + 0.012) % 1.0; $b.Invalidate() } })
    $timer.Start()
    $bar.Add_Disposed({ $timer.Stop(); $timer.Dispose() }.GetNewClosure())
    return $bar
}

function Set-OrionProgress($bar, $value) {
    # $null or a negative value: "working"; otherwise 0..100 and never backwards.
    if ($null -eq $value -or $value -lt 0) { $bar.Tag.Value = -1 }
    else { $bar.Tag.Value = [math]::Max([double]$bar.Tag.Value, [math]::Min(100.0, [double]$value)) }
    $bar.Invalidate()
}

function Show-OrionMessage([string]$text, [string]$title = 'Orion', [switch]$YesNo, [switch]$Warning, [string]$Yes, [string]$No) {
    # Returns 'Yes', 'No' or 'OK'. Falls back to the plain MessageBox if anything here fails.
    try {
        $form = New-OrionForm $title 460 210
        $form.TopMost = $true
        [void](New-OrionHeader $form $title '' 460)
        $body = New-OrionLabel $text 10
        $body.AutoEllipsis = $false
        $measured = [Windows.Forms.TextRenderer]::MeasureText($text, $body.Font, (New-Object Drawing.Size((S 412), 0)), [Windows.Forms.TextFormatFlags]::WordBreak)
        $lines = [math]::Max(1, [math]::Ceiling($measured.Height / [double]$body.Font.Height))
        $height = [int]($lines * $body.Font.Height / $script:OrionScale) + 8
        [void](Add-OrionControl $form $body 24 82 412 $height)
        $top = 82 + $height + 18
        $form.ClientSize = New-Object Drawing.Size((S 460), (S ($top + 52)))
        if ($YesNo) {
            if (-not $Yes) { $Yes = T 'Evet' }
            if (-not $No) { $No = T 'Vazge\u00e7' }
            $ok = New-OrionButton $Yes -Primary:(-not $Warning) -Danger:$Warning
            $ok.DialogResult = 'Yes'
            $cancel = New-OrionButton $No
            $cancel.DialogResult = 'No'
            [void](Add-OrionControl $form $cancel 336 $top 100 34)
            [void](Add-OrionControl $form $ok 226 $top 100 34)
            $form.AcceptButton = $cancel; $form.CancelButton = $cancel
        } else {
            $ok = New-OrionButton (T 'Tamam') -Primary
            $ok.DialogResult = 'OK'
            [void](Add-OrionControl $form $ok 336 $top 100 34)
            $form.AcceptButton = $ok; $form.CancelButton = $ok
        }
        $result = $form.ShowDialog()
        $form.Dispose()
        if ($YesNo) { if ($result -eq 'Yes') { return 'Yes' } else { return 'No' } }
        return 'OK'
    } catch {
        $buttons = if ($YesNo) { 'YesNo' } else { 'OK' }
        $icon = if ($Warning) { 'Warning' } else { 'None' }
        return [string][Windows.Forms.MessageBox]::Show($text, $title, $buttons, $icon)
    }
}

function Set-OrionMenuStyle($menu) {
    if ($script:OrionNative) { $menu.Renderer = New-Object Windows.Forms.ToolStripProfessionalRenderer((New-Object OrionMenuColors)) }
    $menu.BackColor = $script:OrionTheme.Surface
    $menu.ForeColor = $script:OrionTheme.Text
    $menu.Font = Get-OrionFont 10
    $menu.ShowImageMargin = $true
    $menu.Padding = New-Object Windows.Forms.Padding(2, 6, 2, 6)
}
