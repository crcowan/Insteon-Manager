param(
    [string]$ExePath = "$PSScriptRoot\InsteonManager\bin\Debug\net10.0-windows\InsteonManager.exe",
    [string]$WorkingDir = "$PSScriptRoot\InsteonManager"
)

$code = @"
using System;
using System.Runtime.InteropServices;

public class GuiHost {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX;
        public int dwY;
        public int dwXSize;
        public int dwYSize;
        public int dwXCountChars;
        public int dwYCountChars;
        public int dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess;
        public IntPtr hThread;
        public int dwProcessId;
        public int dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateProcess(
        string lpApplicationName,
        string lpCommandLine,
        IntPtr lpProcessAttributes,
        IntPtr lpThreadAttributes,
        bool bInheritHandles,
        uint dwCreationFlags,
        IntPtr lpEnvironment,
        string lpCurrentDirectory,
        ref STARTUPINFO lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    public static int LaunchOnInteractiveDesktop(string appPath, string currentDir) {
        STARTUPINFO si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(si);
        si.lpDesktop = @"WinSta0\Default";

        PROCESS_INFORMATION pi = new PROCESS_INFORMATION();
        bool success = CreateProcess(
            appPath,
            null,
            IntPtr.Zero,
            IntPtr.Zero,
            false,
            0,
            IntPtr.Zero,
            currentDir,
            ref si,
            out pi
        );

        if (!success) {
            int err = Marshal.GetLastWin32Error();
            throw new System.ComponentModel.Win32Exception(err);
        }

        CloseHandle(pi.hThread);
        Console.WriteLine("[INFO] Successfully launched InsteonManager on WinSta0\\Default with PID: " + pi.dwProcessId);

        // Keep this hosting process alive until the user closes the WPF window
        WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
        CloseHandle(pi.hProcess);
        Console.WriteLine("[INFO] InsteonManager window was closed by the user.");
        return 0;
    }
}
"@

if (-not ([System.Management.Automation.PSTypeName]'GuiHost').Type) {
    Add-Type -TypeDefinition $code
}

$fullExe = (Resolve-Path $ExePath).Path
$fullDir = (Resolve-Path $WorkingDir).Path

Write-Host "Starting InsteonManager on interactive desktop..."
[GuiHost]::LaunchOnInteractiveDesktop($fullExe, $fullDir)
