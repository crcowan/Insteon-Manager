using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;

namespace InsteonManager.Services;

public static class ClipboardHelper
{
    public static bool TrySetText(string? text, int retries = 5, int delayMs = 50)
    {
        if (string.IsNullOrEmpty(text))
        {
            return false;
        }

        for (int i = 0; i < retries; i++)
        {
            try
            {
                Clipboard.SetDataObject(text, true);
                return true;
            }
            catch (COMException)
            {
                Thread.Sleep(delayMs);
            }
            catch (Exception)
            {
                break;
            }
        }
        return false;
    }
}
