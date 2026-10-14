using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Media;

namespace InsteonManager.Models;

public enum LinkKind
{
    Controller,  // Source (Hub) controls Target
    Responder,   // Target (Keypad/Sensor) controls Source (Hub)
    CrossLinked  // Both control and respond (bidirectional peer link)
}

public class TopologyLink : INotifyPropertyChanged
{
    private DeviceNode _sourceNode;
    private DeviceNode _targetNode;
    private int _group;
    private LinkKind _kind;
    private string _strokeHex = "#89B4FA";
    private DoubleCollection? _strokeDashArray;
    private string _tooltipText = string.Empty;
    private string _relationshipLabel = string.Empty;

    // Line segment coordinates
    private double _lineX1;
    private double _lineY1;
    private double _lineX2;
    private double _lineY2;
    private double _midX;
    private double _midY;

    // Arrow marker geometries
    private Geometry? _headGeometry;
    private Geometry? _tailGeometry;
    private bool _isVisible = true;

    public TopologyLink(DeviceNode source, DeviceNode target, int group, LinkKind kind)
    {
        _sourceNode = source;
        _targetNode = target;
        _group = group;
        _kind = kind;
        _isVisible = _sourceNode.IsVisible && _targetNode.IsVisible;

        _sourceNode.PropertyChanged += OnNodePropertyChanged;
        _targetNode.PropertyChanged += OnNodePropertyChanged;

        ApplyStyling();
        RecalculateGeometry();
    }

    public bool IsVisible
    {
        get => _isVisible;
        set => SetProperty(ref _isVisible, value);
    }

    public DeviceNode SourceNode
    {
        get => _sourceNode;
        set
        {
            if (_sourceNode != value)
            {
                _sourceNode.PropertyChanged -= OnNodePropertyChanged;
                _sourceNode = value;
                _sourceNode.PropertyChanged += OnNodePropertyChanged;
                OnPropertyChanged();
                RecalculateGeometry();
            }
        }
    }

    public DeviceNode TargetNode
    {
        get => _targetNode;
        set
        {
            if (_targetNode != value)
            {
                _targetNode.PropertyChanged -= OnNodePropertyChanged;
                _targetNode = value;
                _targetNode.PropertyChanged += OnNodePropertyChanged;
                OnPropertyChanged();
                RecalculateGeometry();
            }
        }
    }

    public int Group
    {
        get => _group;
        set => SetProperty(ref _group, value);
    }

    public LinkKind Kind
    {
        get => _kind;
        set
        {
            if (SetProperty(ref _kind, value))
            {
                ApplyStyling();
                RecalculateGeometry();
            }
        }
    }

    public string StrokeHex
    {
        get => _strokeHex;
        set => SetProperty(ref _strokeHex, value);
    }

    public DoubleCollection? StrokeDashArray
    {
        get => _strokeDashArray;
        set => SetProperty(ref _strokeDashArray, value);
    }

    public string TooltipText
    {
        get => _tooltipText;
        set => SetProperty(ref _tooltipText, value);
    }

    public string RelationshipLabel
    {
        get => _relationshipLabel;
        set => SetProperty(ref _relationshipLabel, value);
    }

    public double LineX1
    {
        get => _lineX1;
        private set => SetProperty(ref _lineX1, value);
    }

    public double LineY1
    {
        get => _lineY1;
        private set => SetProperty(ref _lineY1, value);
    }

    public double LineX2
    {
        get => _lineX2;
        private set => SetProperty(ref _lineX2, value);
    }

    public double LineY2
    {
        get => _lineY2;
        private set => SetProperty(ref _lineY2, value);
    }

    public double MidX
    {
        get => _midX;
        private set => SetProperty(ref _midX, value);
    }

    public double MidY
    {
        get => _midY;
        private set => SetProperty(ref _midY, value);
    }

    public Geometry? HeadGeometry
    {
        get => _headGeometry;
        private set => SetProperty(ref _headGeometry, value);
    }

    public Geometry? TailGeometry
    {
        get => _tailGeometry;
        private set => SetProperty(ref _tailGeometry, value);
    }

    private void ApplyStyling()
    {
        switch (Kind)
        {
            case LinkKind.Controller:
                StrokeHex = "#89B4FA"; // Sky Blue
                StrokeDashArray = null; // Solid line
                RelationshipLabel = $"Hub ➔ Controls (Grp {Group})";
                TooltipText = $"HUB (Controller) controls {TargetNode.Address} (Responder) [Scene {Group}]. Arrow points into {TargetNode.Address}.";
                break;

            case LinkKind.Responder:
                StrokeHex = "#A6E3A1"; // Pastel Green
                StrokeDashArray = new DoubleCollection { 3, 3 }; // Dotted line
                RelationshipLabel = $"Keypad ➔ Controls Hub (Grp {Group})";
                TooltipText = $"{TargetNode.Address} (Controller/Keypad) controls HUB (Responder) [Scene {Group}]. Arrow points into Hub.";
                break;

            case LinkKind.CrossLinked:
                StrokeHex = "#F9E2AF"; // Gold / Amber
                StrokeDashArray = new DoubleCollection { 6, 3 }; // Dashed line
                RelationshipLabel = $"◀ 2-Way Peer ▶ (Grp {Group})";
                TooltipText = $"{SourceNode.Address} and {TargetNode.Address} are bidirectional cross-linked peers. [Both control and respond to each other]";
                break;
        }
    }

    private void OnNodePropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(DeviceNode.CenterX) or nameof(DeviceNode.CenterY)
            or nameof(DeviceNode.X) or nameof(DeviceNode.Y))
        {
            RecalculateGeometry();
        }
        else if (e.PropertyName == nameof(DeviceNode.IsVisible))
        {
            IsVisible = _sourceNode.IsVisible && _targetNode.IsVisible;
        }
    }

    public void RecalculateGeometry()
    {
        // 1. Identify Controller (Origin of command signal) vs Responder (Receiver of command signal)
        DeviceNode controllerNode;
        DeviceNode responderNode;

        if (Kind == LinkKind.Responder)
        {
            // Peer is Controller, Hub is Responder
            controllerNode = TargetNode;
            responderNode = SourceNode;
        }
        else
        {
            // Hub is Controller, Peer is Responder (or primary in CrossLinked)
            controllerNode = SourceNode;
            responderNode = TargetNode;
        }

        double cx1 = controllerNode.CenterX;
        double cy1 = controllerNode.CenterY;
        double cx2 = responderNode.CenterX;
        double cy2 = responderNode.CenterY;

        double dx = cx2 - cx1;
        double dy = cy2 - cy1;
        double dist = Math.Sqrt(dx * dx + dy * dy);

        if (dist < 10)
        {
            LineX1 = cx1;
            LineY1 = cy1;
            LineX2 = cx2;
            LineY2 = cy2;
            MidX = cx1;
            MidY = cy1;
            HeadGeometry = null;
            TailGeometry = null;
            return;
        }

        double ux = dx / dist;
        double uy = dy / dist;
        double px = -uy;
        double py = ux;

        // 2. Compute edge intersection points with card bounding boxes (width=160, height=90)
        Point startEdge = GetCardEdge(cx1, cy1, controllerNode.Width / 2.0, controllerNode.Height / 2.0, ux, uy);
        Point endEdge = GetCardEdge(cx2, cy2, responderNode.Width / 2.0, responderNode.Height / 2.0, -ux, -uy);

        // 3. Arrow Head at Responder (Target):
        // Prominent filled triangular arrowhead pointing directly into responder card edge
        double headLen = 16.0;
        double headWidth = 8.0;

        var headGroup = new GeometryGroup();
        var headGeo = new PathGeometry();

        Point headTip = endEdge;
        Point headBase = new Point(endEdge.X - ux * headLen, endEdge.Y - uy * headLen);
        Point headLeft = new Point(headBase.X + px * headWidth, headBase.Y + py * headWidth);
        Point headRight = new Point(headBase.X - px * headWidth, headBase.Y - py * headWidth);

        var headFigure = new PathFigure { StartPoint = headTip, IsClosed = true, IsFilled = true };
        headFigure.Segments.Add(new LineSegment(headLeft, true));
        headFigure.Segments.Add(new LineSegment(headRight, true));
        headGeo.Figures.Add(headFigure);
        headGroup.Children.Add(headGeo);

        // If Cross-Linked, also add an arrowhead at startEdge pointing into the Controller!
        if (Kind == LinkKind.CrossLinked)
        {
            var revHeadGeo = new PathGeometry();
            Point revTip = startEdge;
            Point revBase = new Point(startEdge.X + ux * headLen, startEdge.Y + uy * headLen);
            Point revLeft = new Point(revBase.X + px * headWidth, revBase.Y + py * headWidth);
            Point revRight = new Point(revBase.X - px * headWidth, revBase.Y - py * headWidth);

            var revFigure = new PathFigure { StartPoint = revTip, IsClosed = true, IsFilled = true };
            revFigure.Segments.Add(new LineSegment(revLeft, true));
            revFigure.Segments.Add(new LineSegment(revRight, true));
            revHeadGeo.Figures.Add(revFigure);
            headGroup.Children.Add(revHeadGeo);
        }

        headGroup.Freeze();
        HeadGeometry = headGroup;

        // 4. Arrow Tail at Controller (Origin):
        // Distinct, unambiguous Origin Marker: Flat stop bar + circular anchor dot + forward-pointing feather chevron
        if (Kind != LinkKind.CrossLinked)
        {
            var tailGroup = new GeometryGroup();

            // A) Perpendicular stop bar (card anchor plate)
            double barHalf = 9.0;
            var barGeo = new PathGeometry();
            Point bar1 = new Point(startEdge.X + px * barHalf, startEdge.Y + py * barHalf);
            Point bar2 = new Point(startEdge.X - px * barHalf, startEdge.Y - py * barHalf);
            var barFig = new PathFigure { StartPoint = bar1, IsClosed = false };
            barFig.Segments.Add(new LineSegment(bar2, true));
            barGeo.Figures.Add(barFig);
            tailGroup.Children.Add(barGeo);

            // B) Solid circular origin dot (transmitter anchor)
            Point dotCenter = new Point(startEdge.X + ux * 5.0, startEdge.Y + uy * 5.0);
            var dotGeo = new EllipseGeometry(dotCenter, 4.0, 4.0);
            tailGroup.Children.Add(dotGeo);

            // C) Forward-pointing chevron (points FORWARD along flight direction, NEVER back at the card!)
            double chevronApexDist = 18.0;
            double chevronBackDist = 10.0;
            double chevronSpread = 6.5;

            var chevronGeo = new PathGeometry();
            Point chevronApex = new Point(startEdge.X + ux * chevronApexDist, startEdge.Y + uy * chevronApexDist);
            Point chevronLeft = new Point(startEdge.X + ux * chevronBackDist + px * chevronSpread, startEdge.Y + uy * chevronBackDist + py * chevronSpread);
            Point chevronRight = new Point(startEdge.X + ux * chevronBackDist - px * chevronSpread, startEdge.Y + uy * chevronBackDist - py * chevronSpread);

            var chevFig = new PathFigure { StartPoint = chevronLeft, IsClosed = false };
            chevFig.Segments.Add(new LineSegment(chevronApex, true));
            chevFig.Segments.Add(new LineSegment(chevronRight, true));
            chevronGeo.Figures.Add(chevFig);
            tailGroup.Children.Add(chevronGeo);

            tailGroup.Freeze();
            TailGeometry = tailGroup;
        }
        else
        {
            TailGeometry = null;
        }

        // 5. Line shaft:
        // Connect from controller anchor to base of responder arrowhead
        double startOffset = (Kind == LinkKind.CrossLinked) ? headLen : 5.0;
        LineX1 = startEdge.X + ux * startOffset;
        LineY1 = startEdge.Y + uy * startOffset;
        LineX2 = endEdge.X - ux * headLen;
        LineY2 = endEdge.Y - uy * headLen;

        // 6. Midpoint for scene/group badge
        MidX = (LineX1 + LineX2) / 2.0;
        MidY = (LineY1 + LineY2) / 2.0;
    }

    private static Point GetCardEdge(double cx, double cy, double hw, double hh, double ux, double uy)
    {
        double scaleX = Math.Abs(ux) > 0.0001 ? Math.Abs(hw / ux) : double.MaxValue;
        double scaleY = Math.Abs(uy) > 0.0001 ? Math.Abs(hh / uy) : double.MaxValue;
        double t = Math.Min(scaleX, scaleY);
        return new Point(cx + ux * t, cy + uy * t);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }
}
