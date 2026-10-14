using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using InsteonManager.Models;
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
}