using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using InsteonManager.Models;
using InsteonManager.Services;
using InsteonManager.ViewModels;

namespace InsteonManager;

/// <summary>
/// Interaction logic for MainWindow.xaml
/// </summary>
public partial class MainWindow : Window
{
    private DeviceNode? _draggedNode;
    private Point _startMousePos;
    private double _startNodeX;
    private double _startNodeY;
    private bool _isDragging;

    private bool _isPanningCanvas;
    private Point _panStartMousePos;
    private double _panStartHOffset;
    private double _panStartVOffset;

    private static readonly string LogPath = System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_startup.log");

    public MainWindow()
    {
        InitializeComponent();
        Loaded += MainWindow_Loaded;
        Closing += MainWindow_Closing;
        Closed += (s, e) => System.IO.File.AppendAllText(LogPath, $"[INFO] MainWindow Closed event at {DateTime.Now}\n");
    }

    private static System.Diagnostics.Process? _localBackendProcess;

    private async Task EnsureLocalSimulatorBackendRunningAsync(string backendUrl)
    {
        try
        {
            if (!Uri.TryCreate(backendUrl, UriKind.Absolute, out var uri))
                return;

            if (!uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase) &&
                !uri.Host.Equals("127.0.0.1", StringComparison.OrdinalIgnoreCase))
            {
                return; // Connecting to remote Raspberry Pi production backend
            }

            int port = uri.Port > 0 ? uri.Port : 8000;
            using (var tcp = new System.Net.Sockets.TcpClient())
            {
                var connectTask = tcp.ConnectAsync("127.0.0.1", port);
                if (await Task.WhenAny(connectTask, Task.Delay(350)) == connectTask && tcp.Connected)
                {
                    return; // Local backend is already running
                }
            }

            // Walk up directory tree from BaseDirectory to locate repo root containing backend\.venv\Scripts\python.exe
            var dir = new System.IO.DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            while (dir != null)
            {
                var backendDir = System.IO.Path.Combine(dir.FullName, "backend");
                var pythonExe = System.IO.Path.Combine(backendDir, ".venv", "Scripts", "python.exe");
                var mainPy = System.IO.Path.Combine(backendDir, "app", "main.py");
                if (System.IO.File.Exists(pythonExe) && System.IO.File.Exists(mainPy))
                {
                    var psi = new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = pythonExe,
                        Arguments = $"-m uvicorn app.main:app --host 127.0.0.1 --port {port}",
                        WorkingDirectory = backendDir,
                        CreateNoWindow = true,
                        UseShellExecute = false
                    };
                    psi.EnvironmentVariables["MOCK_MODE"] = "true";
                    _localBackendProcess = System.Diagnostics.Process.Start(psi);
                    System.IO.File.AppendAllText(LogPath, $"[INFO] Auto-started local simulator backend (PID {_localBackendProcess?.Id}) at {DateTime.Now}\n");

                    // Wait up to 4 seconds for port to listen
                    for (int i = 0; i < 16; i++)
                    {
                        await Task.Delay(250);
                        using var checkTcp = new System.Net.Sockets.TcpClient();
                        var checkTask = checkTcp.ConnectAsync("127.0.0.1", port);
                        if (await Task.WhenAny(checkTask, Task.Delay(200)) == checkTask && checkTcp.Connected)
                        {
                            break;
                        }
                    }
                    break;
                }
                dir = dir.Parent;
            }
        }
        catch (Exception ex)
        {
            System.IO.File.AppendAllText(LogPath, $"[WARN] EnsureLocalSimulatorBackendRunningAsync error: {ex.Message}\n");
        }
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        System.IO.File.AppendAllText(LogPath, $"[INFO] MainWindow Loaded event at {DateTime.Now}\n");

        // Center canvas view around PLM modem (1200, 900)
        _ = Dispatcher.BeginInvoke(new Action(() =>
        {
            if (GraphScrollViewer.ActualWidth > 0 && GraphScrollViewer.ActualHeight > 0)
            {
                GraphScrollViewer.ScrollToHorizontalOffset(Math.Max(0, 1200 - (GraphScrollViewer.ActualWidth / 2)));
                GraphScrollViewer.ScrollToVerticalOffset(Math.Max(0, 900 - (GraphScrollViewer.ActualHeight / 2)));
            }
        }), System.Windows.Threading.DispatcherPriority.Loaded);

        if (DataContext is MainViewModel vm)
        {
            await EnsureLocalSimulatorBackendRunningAsync(vm.BackendUrl);
            await vm.CheckStatusAsync();
            await vm.QueryAldbAsync();
        }
    }

    private bool _isClosingInProgress = false;

    private async void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        System.IO.File.AppendAllText(LogPath, $"[INFO] MainWindow Closing event at {DateTime.Now}, isClosingInProgress={_isClosingInProgress}\n");
        if (_isClosingInProgress)
        {
            return;
        }

        if (DataContext is MainViewModel vm)
        {
            vm.SaveNodePositions();

            if (vm.IsSessionActive)
            {
                // Cancel immediate window termination to allow async network release
                e.Cancel = true;
                _isClosingInProgress = true;

                vm.StatusMessage = "Stopping session and restoring openHAB before exit...";
                vm.IsLoading = true;

                try
                {
                    await vm.ReleaseHardwareOnExitAsync();
                }
                finally
                {
                    Close();
                }
            }
        }
    }

    private void Node_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement element && element.DataContext is DeviceNode node)
        {
            if (DataContext is MainViewModel vm)
            {
                vm.SelectGraphNode(node);
            }
            _draggedNode = node;
            _startMousePos = e.GetPosition(GraphCanvas);
            _startNodeX = node.X;
            _startNodeY = node.Y;
            _isDragging = true;
            element.CaptureMouse();
            e.Handled = true;
        }
    }

    private void Node_MouseRightButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement element && element.DataContext is DeviceNode node)
        {
            if (DataContext is MainViewModel vm)
            {
                vm.SelectGraphNode(node);
            }
            // Do not set e.Handled = true so that the ContextMenu opens normally
        }
    }

    private void Node_MouseMove(object sender, MouseEventArgs e)
    {
        if (_isDragging && _draggedNode != null && sender is FrameworkElement)
        {
            var currentPos = e.GetPosition(GraphCanvas);
            double deltaX = currentPos.X - _startMousePos.X;
            double deltaY = currentPos.Y - _startMousePos.Y;

            _draggedNode.X = Math.Max(10, Math.Min(2420, _startNodeX + deltaX));
            _draggedNode.Y = Math.Max(10, Math.Min(1820, _startNodeY + deltaY));
            e.Handled = true;
        }
    }

    private void Node_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (_isDragging && sender is FrameworkElement element)
        {
            element.ReleaseMouseCapture();
            _isDragging = false;
            _draggedNode = null;

            if (DataContext is MainViewModel vm)
            {
                vm.SaveNodePositions();
            }
            e.Handled = true;
        }
    }

    private void GraphScrollViewer_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        if (Keyboard.Modifiers == ModifierKeys.Control || _draggedNode == null)
        {
            if (DataContext is MainViewModel vm)
            {
                if (e.Delta > 0)
                {
                    vm.ZoomIn();
                }
                else if (e.Delta < 0)
                {
                    vm.ZoomOut();
                }
                e.Handled = true;
            }
        }
    }

    private void GraphCanvas_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (_isDragging) return;

        StartCanvasPan(e.GetPosition(GraphScrollViewer));
        e.Handled = true;
    }

    private void GraphCanvas_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ChangedButton == MouseButton.Middle && !_isDragging)
        {
            StartCanvasPan(e.GetPosition(GraphScrollViewer));
            e.Handled = true;
        }
    }

    private void StartCanvasPan(Point mousePos)
    {
        _isPanningCanvas = true;
        _panStartMousePos = mousePos;
        _panStartHOffset = GraphScrollViewer.HorizontalOffset;
        _panStartVOffset = GraphScrollViewer.VerticalOffset;
        GraphCanvas.CaptureMouse();
        GraphCanvas.Cursor = Cursors.SizeAll;
    }

    private void GraphCanvas_MouseMove(object sender, MouseEventArgs e)
    {
        if (_isPanningCanvas)
        {
            var currentPos = e.GetPosition(GraphScrollViewer);
            double deltaX = currentPos.X - _panStartMousePos.X;
            double deltaY = currentPos.Y - _panStartMousePos.Y;

            GraphScrollViewer.ScrollToHorizontalOffset(_panStartHOffset - deltaX);
            GraphScrollViewer.ScrollToVerticalOffset(_panStartVOffset - deltaY);
            e.Handled = true;
        }
    }

    private void GraphCanvas_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (_isPanningCanvas)
        {
            EndCanvasPan();
            e.Handled = true;
        }
    }

    private void GraphCanvas_MouseUp(object sender, MouseButtonEventArgs e)
    {
        if (e.ChangedButton == MouseButton.Middle && _isPanningCanvas)
        {
            EndCanvasPan();
            e.Handled = true;
        }
    }

    private void EndCanvasPan()
    {
        GraphCanvas.ReleaseMouseCapture();
        _isPanningCanvas = false;
        GraphCanvas.Cursor = Cursors.Hand;
    }

    private async void SaveMemberPresetButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button btn && btn.DataContext is SceneMemberModel member && DataContext is MainViewModel vm)
        {
            await vm.UpdateMemberPresetAsync(member);
        }
    }

    private DeviceNode? GetNodeFromMenuItem(object sender)
    {
        if (sender is MenuItem mi)
        {
            if (mi.CommandParameter is DeviceNode dn1) return dn1;
            if (mi.DataContext is DeviceNode dn2) return dn2;
            if (mi.Parent is ContextMenu cm && cm.PlacementTarget is FrameworkElement fe && fe.DataContext is DeviceNode dn3)
                return dn3;
        }
        return null;
    }

    private void MenuItem_SetController_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            var node = GetNodeFromMenuItem(sender) ?? vm.SelectedGraphNode;
            if (node != null)
            {
                vm.SetSelectedNodeAsController(node);
            }
        }
    }

    private void MenuItem_SetResponder_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            var node = GetNodeFromMenuItem(sender) ?? vm.SelectedGraphNode;
            if (node != null)
            {
                vm.SetSelectedNodeAsResponder(node);
            }
        }
    }

    private void MenuItem_SetReplaceOld_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            var node = GetNodeFromMenuItem(sender) ?? vm.SelectedGraphNode;
            if (node != null)
            {
                vm.SetSelectedNodeAsReplaceOld(node);
            }
        }
    }

    private void MenuItem_InspectAldb_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            var node = GetNodeFromMenuItem(sender) ?? vm.SelectedGraphNode;
            if (node != null)
            {
                vm.InspectNodeInAldb(node.Address);
            }
        }
    }

    private void MenuItem_CopyAddress_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            var node = GetNodeFromMenuItem(sender) ?? vm.SelectedGraphNode;
            if (node != null)
            {
                if (ClipboardHelper.TrySetText(node.Address))
                {
                    vm.StatusMessage = $"Copied {node.Address} to clipboard.";
                    vm.AddLog(LogLevel.Info, "UI", $"Copied {node.Address} to clipboard.");
                }
                else
                {
                    vm.StatusMessage = $"Unable to access clipboard to copy {node.Address}.";
                }
            }
        }
    }
}