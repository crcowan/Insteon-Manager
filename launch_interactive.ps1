Add-Type @"
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

public class ExplorerChildLauncher {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFOEX {
        public STARTUPINFO StartupInfo;
        public IntPtr lpAttributeList;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public Int32 cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public Int32 dwX;
        public Int32 dwY;
        public Int32 dwXSize;
        public Int32 dwYSize;
        public Int32 dwXCountChars;
        public Int32 dwYCountChars;
        public Int32 dwFillAttribute;
        public Int32 dwFlags;
        public Int16 wShowWindow;
        public Int16 cbReserved2;
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

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr OpenProcess(uint processAccess, bool bInheritHandle, int processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool InitializeProcThreadAttributeList(IntPtr lpAttributeList, int dwAttributeCount, int dwFlags, ref IntPtr lpSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool UpdateProcThreadAttribute(IntPtr lpAttributeList, uint dwFlags, IntPtr Attribute, IntPtr lpValue, IntPtr cbSize, IntPtr lpPreviousValue, IntPtr lpReturnSize);

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
        ref STARTUPINFOEX lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool DeleteProcThreadAttributeList(IntPtr lpAttributeList);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    const uint PROCESS_CREATE_PROCESS = 0x0080;
    const int PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000;

    public static int LaunchViaExplorer(string exePath, string workingDir) {
        Process[] explorers = Process.GetProcessesByName("explorer");
        if (explorers.Length == 0) throw new Exception("Explorer process not found");

        IntPtr hExplorer = OpenProcess(PROCESS_CREATE_PROCESS, false, explorers[0].Id);
        if (hExplorer == IntPtr.Zero) throw new Exception("Failed to open Explorer process");

        IntPtr lpSize = IntPtr.Zero;
        InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref lpSize);

        IntPtr lpAttributeList = Marshal.AllocHGlobal(lpSize);
        InitializeProcThreadAttributeList(lpAttributeList, 1, 0, ref lpSize);

        IntPtr lpValue = Marshal.AllocHGlobal(IntPtr.Size);
        Marshal.WriteIntPtr(lpValue, hExplorer);

        UpdateProcThreadAttribute(lpAttributeList, 0, (IntPtr)PROC_THREAD_ATTRIBUTE_PARENT_PROCESS, lpValue, (IntPtr)IntPtr.Size, IntPtr.Zero, IntPtr.Zero);

        STARTUPINFOEX siex = new STARTUPINFOEX();
        siex.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
        siex.StartupInfo.lpDesktop = @"WinSta0\Default";
        siex.lpAttributeList = lpAttributeList;

        PROCESS_INFORMATION pi = new PROCESS_INFORMATION();
        const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;

        string cmd = "\"" + exePath + "\"";
        bool success = CreateProcess(exePath, cmd, IntPtr.Zero, IntPtr.Zero, false, EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, workingDir, ref siex, out pi);

        int err = success ? 0 : Marshal.GetLastWin32Error();

        DeleteProcThreadAttributeList(lpAttributeList);
        Marshal.FreeHGlobal(lpAttributeList);
        Marshal.FreeHGlobal(lpValue);
        CloseHandle(hExplorer);

        if (!success) throw new Exception("CreateProcess failed with error " + err);

        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return pi.dwProcessId;
    }
}
"@

$exe = "D:\Additional Documents\Antigravity Projects\Insteon GUI\frontend\InsteonManager\bin\Debug\net10.0-windows\InsteonManager.exe"
$workingDir = "D:\Additional Documents\Antigravity Projects\Insteon GUI\frontend\InsteonManager\bin\Debug\net10.0-windows"

$childPid = [ExplorerChildLauncher]::LaunchViaExplorer($exe, $workingDir)
Write-Output "Successfully launched InsteonManager as child of Explorer with PID: $childPid"
