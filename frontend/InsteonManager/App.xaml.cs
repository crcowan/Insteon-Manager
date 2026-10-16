using System.Windows;
using System.IO;

namespace InsteonManager;

/// <summary>
/// Interaction logic for App.xaml
/// </summary>
public partial class App : Application
{
    private static readonly string LogPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_startup.log");
    private static readonly string ErrorPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "startup_error.log");

    public App()
    {
        DispatcherUnhandledException += (s, e) =>
        {
            try
            {
                File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "crash_dispatcher.log"), e.Exception.ToString());
            }
            catch { }
            e.Handled = true;
        };

        AppDomain.CurrentDomain.UnhandledException += (s, e) =>
        {
            File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "crash_domain.log"), e.ExceptionObject.ToString());
        };

        AppDomain.CurrentDomain.ProcessExit += (s, e) =>
        {
            File.AppendAllText(LogPath, $"[INFO] ProcessExit at {DateTime.Now}\nStackTrace: {Environment.StackTrace}\n");
        };
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        File.WriteAllText(LogPath, $"[INFO] OnStartup called at {DateTime.Now}\n");
        base.OnStartup(e);
        File.AppendAllText(LogPath, $"[INFO] base.OnStartup completed at {DateTime.Now}\n");
    }

    protected override void OnExit(ExitEventArgs e)
    {
        File.AppendAllText(LogPath, $"[INFO] OnExit called with code {e.ApplicationExitCode} at {DateTime.Now}\n");
        base.OnExit(e);
    }
}
