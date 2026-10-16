using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Threading;
using InsteonManager.Models;
using InsteonManager.Services;

namespace InsteonManager.ViewModels;

public class MainViewModel : ViewModelBase
{
    private readonly InsteonApiClient _apiClient;
    private readonly NodePositionService _positionService;
    private readonly DispatcherTimer _heartbeatTimer;
    private readonly DispatcherTimer _statusPollTimer;
    private readonly string _clientId;

    private string _backendUrl = "http://localhost:8000";
    private bool _isSessionActive;
    private string _sessionStatusText = "Session Inactive";
    private string _openhabStatusText = "openHAB Online (Holding Lock)";
    private string _statusMessage = "Ready. Configure Backend URL and start a session to manage Insteon network.";
    private bool _isLoading;
    private bool _forceRefreshHardware;
    private int _remainingLeaseSeconds;
    private DeviceNode? _selectedNode;
    private string _filterText = string.Empty;
    private List<DeviceSummaryModel> _currentDevices = new();

    private double _zoomLevel = 1.0;
    private double _spreadDistance = 520.0;
    private string _deviceFilterText = string.Empty;
    private string _aldbSearchText = string.Empty;
    private int _selectedDevicesCount;

    private string _mapNodeFilterText = string.Empty;
    private bool _includeConnectedPeers;
    private int _visibleNodesCount;
    private int _visibleLinksCount;

    public MainViewModel()
    {
        _apiClient = new InsteonApiClient();
        _positionService = new NodePositionService();
        _clientId = $"win-client-{Environment.MachineName}";

        AldbRecords = new ObservableCollection<AldbRecordModel>();
        FilteredAldbRecords = new ObservableCollection<AldbRecordModel>();
        DeviceNodes = new ObservableCollection<DeviceNode>();
        FilteredMapNodes = new ObservableCollection<DeviceNode>();
        TopologyLinks = new ObservableCollection<TopologyLink>();
        LogEntries = new ObservableCollection<LogEntryModel>();
        StagedChanges = new ObservableCollection<StagedChangeModel>();

        SelectableDevices = new ObservableCollection<SelectableDeviceItem>();
        FilteredSelectableDevices = new ObservableCollection<SelectableDeviceItem>();
        OrganizedDevices = new ObservableCollection<OrganizedDeviceAldb>();

        Scenes = new ObservableCollection<SceneModel>();
        FilteredScenes = new ObservableCollection<SceneModel>();
        GhostTargets = new ObservableCollection<string>();
        ActiveLinkPairs = new ObservableCollection<ActiveLinkPairModel>();
        MissingDefaultLinks = new ObservableCollection<MissingDefaultLinkItemModel>();

        DeviceCategoryOptions = new ObservableCollection<DeviceCategoryOption>
        {
            new() { Name = "Dimmer Switch 2477D", Cat = 1, Subcat = 32, Description = "Dimmable Wall Switch / Keypad" },
            new() { Name = "On/Off Switch 2477S", Cat = 2, Subcat = 42, Description = "Relay Switch (Non-Dimmable)" },
            new() { Name = "KeypadLinc 6-Button 2487S", Cat = 1, Subcat = 27, Description = "Keypad Dimmer (6-Button)" },
            new() { Name = "KeypadLinc 8-Button 2486D", Cat = 1, Subcat = 28, Description = "Keypad Dimmer (8-Button)" },
            new() { Name = "LampLinc Dimmer 2457D2", Cat = 1, Subcat = 14, Description = "Plug-in Lamp Dimmer Module" },
            new() { Name = "ApplianceLinc Relay 2456S3", Cat = 2, Subcat = 9, Description = "Plug-in Appliance On/Off Module" },
            new() { Name = "OutletLinc Dimmer 2472D", Cat = 1, Subcat = 33, Description = "In-Wall Receptacle Dimmer" },
            new() { Name = "In-LineLinc Relay 2475SDB", Cat = 2, Subcat = 22, Description = "Hardwired In-Line Relay" },
            new() { Name = "FanLinc Dual-Load 2449U", Cat = 1, Subcat = 46, Description = "Ceiling Fan & Light Controller" },
            new() { Name = "Battery Sensor / Remote", Cat = 16, Subcat = 1, Description = "Wireless Sensor / Remote" },
            new() { Name = "Generic / Custom Insteon Device", Cat = 1, Subcat = 0, Description = "Custom Category" }
        };
        _selectedDeviceCategory = DeviceCategoryOptions[0];

        // Heartbeat timer (every 30 seconds while session is active)
        _heartbeatTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(30)
        };
        _heartbeatTimer.Tick += async (s, e) => await OnHeartbeatTickAsync();

        // Status poll timer (every 5 seconds to sync status)
        _statusPollTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(5)
        };
        _statusPollTimer.Tick += async (s, e) => await CheckStatusAsync();
        _statusPollTimer.Start();

        // Commands
        ToggleSessionCommand = new AsyncRelayCommand(ToggleSessionAsync, () => !IsLoading);
        StartSessionCommand = new AsyncRelayCommand(StartSessionAsync, () => !IsSessionActive && !IsLoading);
        EndSessionCommand = new AsyncRelayCommand(EndSessionAsync, () => IsSessionActive && !IsLoading);
        QueryAldbCommand = new AsyncRelayCommand(() => QueryAldbAsync(false), () => !IsLoading);
        ForceRestoreOpenhabCommand = new AsyncRelayCommand(ForceRestoreOpenhabAsync, () => !IsLoading);
        CheckConnectionCommand = new AsyncRelayCommand(CheckStatusAsync, () => !IsLoading);
        ResetLayoutCommand = new RelayCommand(ResetLayout);
        SpreadLayoutCommand = new RelayCommand(SpreadLayout);
        ClearLogsCommand = new RelayCommand(ClearLogs);
        StageSampleChangeCommand = new RelayCommand(StageSampleChange);
        CommitStagedChangesCommand = new AsyncRelayCommand(CommitStagedChangesAsync, () => StagedChanges.Count > 0 && !IsLoading);
        DiscardStagedChangesCommand = new RelayCommand(DiscardStagedChanges);
        RemoveStagedChangeCommand = new RelayCommand(obj =>
        {
            if (obj is StagedChangeModel sc)
            {
                StagedChanges.Remove(sc);
                AddLog(LogLevel.Info, "Staging", $"Removed staged change '{sc.Id}' ({sc.Operation}).");
                StatusMessage = $"Removed staged change {sc.Id}.";
                CommitStagedChangesCommand?.NotifyCanExecuteChanged();
            }
        });
        StagedChanges.CollectionChanged += (s, e) => CommitStagedChangesCommand?.NotifyCanExecuteChanged();

        StageReciprocalLinkCommand = new RelayCommand(StageReciprocalLink, () => !string.IsNullOrEmpty(NewLinkControllerAddress) && !string.IsNullOrEmpty(NewLinkResponderAddress));
        StageDeleteLinkCommand = new RelayCommand(StageDeleteLink, () => !string.IsNullOrEmpty(DeleteLinkControllerAddress) && !string.IsNullOrEmpty(DeleteLinkResponderAddress));
        StageDeviceReplaceCommand = new RelayCommand(StageDeviceReplace, () => !string.IsNullOrEmpty(ReplaceOldDeviceAddress) && !string.IsNullOrEmpty(ReplaceNewDeviceAddress));

        AuditDefaultLinksCommand = new AsyncRelayCommand(() => AuditDefaultLinksAsync(false), () => !IsLoading);
        StageDefaultLinkForDeviceCommand = new RelayCommand(obj => StageDefaultLinkForDevice(obj));
        FixDefaultLinkForDeviceCommand = new AsyncRelayCommand<object>(FixDefaultLinkForDeviceAsync, _ => !IsLoading);
        StageFixAllDefaultLinksCommand = new RelayCommand(StageFixAllDefaultLinks, () => MissingDefaultLinks.Count > 0 && !IsLoading);
        StageAddDeviceCommand = new RelayCommand(StageAddDevice, () => InsteonAddressHelper.IsValid(NewDeviceAddress));
        AddDeviceNowCommand = new AsyncRelayCommand(AddDeviceNowAsync, () => InsteonAddressHelper.IsValid(NewDeviceAddress) && !IsLoading);

        AnalyzeNetworkCommand = new AsyncRelayCommand(() => RunNetworkAnalysisAsync(false), () => !IsLoading && !IsAnalyzingNetwork);
        FilterAnalyzerCommand = new RelayCommand<string>(filter => { if (filter != null) SelectedAnalyzerFilter = filter; });
        FixNetworkIssueCommand = new AsyncRelayCommand<NetworkIssueItemModel>(FixNetworkIssueAsync, issue => issue != null && !IsLoading);
        DeleteNetworkIssueSlotCommand = new AsyncRelayCommand<NetworkIssueModel>(DeleteNetworkIssueSlotAsync, issue => issue != null && !string.IsNullOrEmpty(issue.MemAddr) && !IsLoading);
        StageNetworkIssueFixCommand = new RelayCommand<NetworkIssueModel>(StageNetworkIssueFix, issue => issue != null);
        FixAllHalfLinksCommand = new AsyncRelayCommand(FixAllHalfLinksAsync, () => AnalyzerHalfLinksCount > 0 && !IsLoading);
        StageAllAnalyzerFixesCommand = new RelayCommand(StageAllAnalyzerFixes, () => NetworkIssues.Count > 0 && !IsLoading);

        RefreshScenesCommand = new AsyncRelayCommand(() => LoadScenesAsync(false), () => !IsLoading);
        TriggerSceneOnCommand = new AsyncRelayCommand(() => TriggerSelectedSceneAsync("on"), () => SelectedScene != null && !IsLoading);
        TriggerSceneOffCommand = new AsyncRelayCommand(() => TriggerSelectedSceneAsync("off"), () => SelectedScene != null && !IsLoading);
        TriggerSceneFastOnCommand = new AsyncRelayCommand(() => TriggerSelectedSceneAsync("fast_on"), () => SelectedScene != null && !IsLoading);
        TriggerSceneFastOffCommand = new AsyncRelayCommand(() => TriggerSelectedSceneAsync("fast_off"), () => SelectedScene != null && !IsLoading);
        PurgeSelectedGhostTargetCommand = new AsyncRelayCommand(PurgeSelectedGhostTargetAsync, () => !string.IsNullOrEmpty(SelectedGhostTarget) && !IsLoading);
        ExecuteDeviceReplaceCommand = new AsyncRelayCommand(ExecuteDeviceReplaceAsync, () => !string.IsNullOrEmpty(ReplaceOldDeviceAddress) && !string.IsNullOrEmpty(ReplaceNewDeviceAddress) && !IsLoading);
        CreateReciprocalLinkCommand = new AsyncRelayCommand(CreateReciprocalLinkAsync, () => !string.IsNullOrEmpty(NewLinkControllerAddress) && !string.IsNullOrEmpty(NewLinkResponderAddress) && !IsLoading);
        DeleteReciprocalLinkCommand = new AsyncRelayCommand(DeleteReciprocalLinkAsync, () => !string.IsNullOrEmpty(DeleteLinkControllerAddress) && !string.IsNullOrEmpty(DeleteLinkResponderAddress) && !IsLoading);
        DeleteLinkFromEntryCommand = new AsyncRelayCommand<OrganizedAldbEntry>(DeleteLinkFromEntryAsync, entry => entry != null && !IsLoading);

        ControlDeviceCommand = new AsyncRelayCommand<string>(ControlDeviceFromStringAsync, _ => !IsLoading);
        ControlSelectedNodeCommand = new AsyncRelayCommand<string>(ControlSelectedNodeAsync, _ => SelectedGraphNode != null && !IsLoading);
        QuickTriggerSceneCommand = new AsyncRelayCommand<string>(QuickTriggerSceneAsync, _ => SelectedQuickScene != null && !IsLoading);
        SelectGraphNodeCommand = new RelayCommand(obj =>
        {
            if (obj is DeviceNode node)
            {
                SelectGraphNode(node);
            }
        });

        SetSelectedNodeAsControllerCommand = new RelayCommand(obj =>
        {
            var node = obj as DeviceNode ?? SelectedGraphNode;
            if (node != null)
            {
                SetSelectedNodeAsController(node);
            }
        });

        SetSelectedNodeAsResponderCommand = new RelayCommand(obj =>
        {
            var node = obj as DeviceNode ?? SelectedGraphNode;
            if (node != null)
            {
                SetSelectedNodeAsResponder(node);
            }
        });

        SetSelectedNodeAsReplaceOldCommand = new RelayCommand(obj =>
        {
            var node = obj as DeviceNode ?? SelectedGraphNode;
            if (node != null)
            {
                SetSelectedNodeAsReplaceOld(node);
            }
        });

        InspectNodeInAldbCommand = new RelayCommand(obj =>
        {
            string? targetAddr = (obj as DeviceNode)?.Address ?? (obj as string) ?? SelectedGraphNode?.Address;
            if (!string.IsNullOrWhiteSpace(targetAddr))
            {
                InspectNodeInAldb(targetAddr);
            }
        });

        CopyNodeAddressCommand = new RelayCommand(obj =>
        {
            string? addr = (obj as DeviceNode)?.Address ?? (obj as string) ?? SelectedGraphNode?.Address;
            if (!string.IsNullOrWhiteSpace(addr))
            {
                if (ClipboardHelper.TrySetText(addr))
                {
                    StatusMessage = $"Copied Insteon address {addr} to clipboard.";
                    AddLog(LogLevel.Info, "UI", $"Copied {addr} to clipboard.");
                }
                else
                {
                    StatusMessage = $"Unable to access clipboard to copy {addr}.";
                }
            }
        });

        SwapNewLinkAddressesCommand = new RelayCommand(() =>
        {
            var temp = NewLinkControllerAddress;
            NewLinkControllerAddress = NewLinkResponderAddress;
            NewLinkResponderAddress = temp;
            StatusMessage = $"Swapped Controller and Responder ({NewLinkControllerAddress} <-> {NewLinkResponderAddress}).";
            AddLog(LogLevel.Info, "NetworkTools", $"Swapped roles in Link Creator: Controller={NewLinkControllerAddress}, Responder={NewLinkResponderAddress}.");
        });

        SwapDeleteLinkAddressesCommand = new RelayCommand(() =>
        {
            var temp = DeleteLinkControllerAddress;
            DeleteLinkControllerAddress = DeleteLinkResponderAddress;
            DeleteLinkResponderAddress = temp;
            StatusMessage = $"Swapped Delete Controller and Responder ({DeleteLinkControllerAddress} <-> {DeleteLinkResponderAddress}).";
        });

        ApplySelectedGraphNodeCommand = new RelayCommand(obj =>
        {
            if (SelectedGraphNode == null)
            {
                StatusMessage = "No node is currently selected on the topology graph.";
                return;
            }
            var addr = SelectedGraphNode.Address;
            var targetField = (obj as string)?.ToLowerInvariant();
            switch (targetField)
            {
                case "new_ctrl":
                    NewLinkControllerAddress = addr;
                    break;
                case "new_resp":
                    NewLinkResponderAddress = addr;
                    break;
                case "del_ctrl":
                    DeleteLinkControllerAddress = addr;
                    break;
                case "del_resp":
                    DeleteLinkResponderAddress = addr;
                    break;
                case "rep_old":
                    ReplaceOldDeviceAddress = addr;
                    break;
                case "rep_new":
                    ReplaceNewDeviceAddress = addr;
                    break;
            }
            StatusMessage = $"Applied {SelectedGraphNode.FriendlyName} ({addr}) to {targetField}.";
        });

        ZoomInCommand = new RelayCommand(ZoomIn);
        ZoomOutCommand = new RelayCommand(ZoomOut);
        ResetZoomCommand = new RelayCommand(ResetZoom);
        SelectAllDevicesCommand = new RelayCommand(() => SelectAllDevices(true));
        DeselectAllDevicesCommand = new RelayCommand(() => SelectAllDevices(false));
        SelectOnlyLineDevicesCommand = new RelayCommand(SelectOnlyLineDevices);

        ShowAllMapNodesCommand = new RelayCommand(ShowAllMapNodes);
        ClearAllMapNodesCommand = new RelayCommand(ClearAllMapNodes);
        SelectLineOnlyMapNodesCommand = new RelayCommand(SelectLineOnlyMapNodes);

        // Initial setup
        UpdateBackendUrl();
        AddLog(LogLevel.Info, "System", "Insteon Network Management Bridge GUI started.");
    }

    public string BackendUrl
    {
        get => _backendUrl;
        set
        {
            if (SetProperty(ref _backendUrl, value))
            {
                UpdateBackendUrl();
            }
        }
    }

    public bool IsSessionActive
    {
        get => _isSessionActive;
        set
        {
            if (SetProperty(ref _isSessionActive, value))
            {
                OnPropertyChanged(nameof(SessionToggleButtonText));
                OnPropertyChanged(nameof(SessionToggleButtonToolTip));
            }
        }
    }

    public string SessionStatusText
    {
        get => _sessionStatusText;
        set => SetProperty(ref _sessionStatusText, value);
    }

    public string OpenhabStatusText
    {
        get => _openhabStatusText;
        set => SetProperty(ref _openhabStatusText, value);
    }

    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    public bool IsLoading
    {
        get => _isLoading;
        set => SetProperty(ref _isLoading, value);
    }

    public bool ForceRefreshHardware
    {
        get => _forceRefreshHardware;
        set => SetProperty(ref _forceRefreshHardware, value);
    }

    public int RemainingLeaseSeconds
    {
        get => _remainingLeaseSeconds;
        set => SetProperty(ref _remainingLeaseSeconds, value);
    }

    public DeviceNode? SelectedNode
    {
        get => _selectedNode;
        set => SetProperty(ref _selectedNode, value);
    }

    public string FilterText
    {
        get => _filterText;
        set
        {
            if (SetProperty(ref _filterText, value))
            {
                ApplyFilter();
            }
        }
    }

    public ObservableCollection<AldbRecordModel> AldbRecords { get; }
    public ObservableCollection<AldbRecordModel> FilteredAldbRecords { get; }
    public ObservableCollection<DeviceNode> DeviceNodes { get; }
    public ObservableCollection<DeviceNode> FilteredMapNodes { get; }
    public ObservableCollection<TopologyLink> TopologyLinks { get; }
    public ObservableCollection<LogEntryModel> LogEntries { get; }
    public ObservableCollection<StagedChangeModel> StagedChanges { get; }

    public ObservableCollection<SelectableDeviceItem> SelectableDevices { get; }
    public ObservableCollection<SelectableDeviceItem> FilteredSelectableDevices { get; }
    public ObservableCollection<OrganizedDeviceAldb> OrganizedDevices { get; }

    public double ZoomLevel
    {
        get => _zoomLevel;
        set
        {
            if (SetProperty(ref _zoomLevel, Math.Clamp(value, 0.3, 2.5)))
            {
                OnPropertyChanged(nameof(ZoomPercentageText));
            }
        }
    }

    public string ZoomPercentageText => $"{(int)(_zoomLevel * 100)}%";

    public double SpreadDistance
    {
        get => _spreadDistance;
        set
        {
            if (SetProperty(ref _spreadDistance, Math.Clamp(value, 280.0, 900.0)))
            {
                OnPropertyChanged(nameof(SpreadDistanceText));
            }
        }
    }

    public string SpreadDistanceText => $"{(int)_spreadDistance} px";

    public string DeviceFilterText
    {
        get => _deviceFilterText;
        set
        {
            if (SetProperty(ref _deviceFilterText, value))
            {
                ApplyDeviceFilter();
            }
        }
    }

    public string AldbSearchText
    {
        get => _aldbSearchText;
        set
        {
            if (SetProperty(ref _aldbSearchText, value))
            {
                RebuildOrganizedAldbView();
            }
        }
    }

    public int SelectedDevicesCount
    {
        get => _selectedDevicesCount;
        set => SetProperty(ref _selectedDevicesCount, value);
    }

    public string MapNodeFilterText
    {
        get => _mapNodeFilterText;
        set
        {
            if (SetProperty(ref _mapNodeFilterText, value))
            {
                ApplyMapNodeSearchFilter();
            }
        }
    }

    public bool IncludeConnectedPeers
    {
        get => _includeConnectedPeers;
        set
        {
            if (SetProperty(ref _includeConnectedPeers, value))
            {
                UpdateMapVisibility();
            }
        }
    }

    public int VisibleNodesCount
    {
        get => _visibleNodesCount;
        set
        {
            if (SetProperty(ref _visibleNodesCount, value))
            {
                OnPropertyChanged(nameof(IsMapFiltered));
                OnPropertyChanged(nameof(MapFilterStatusText));
            }
        }
    }

    public int VisibleLinksCount
    {
        get => _visibleLinksCount;
        set => SetProperty(ref _visibleLinksCount, value);
    }

    public bool IsMapFiltered => DeviceNodes.Count > 0 && VisibleNodesCount < DeviceNodes.Count;

    public string MapFilterStatusText => IsMapFiltered
        ? $"Filtered: {VisibleNodesCount} of {DeviceNodes.Count} nodes visible"
        : $"Showing All ({DeviceNodes.Count} nodes)";

    public AsyncRelayCommand ToggleSessionCommand { get; }
    public AsyncRelayCommand StartSessionCommand { get; }
    public AsyncRelayCommand EndSessionCommand { get; }

    public string SessionToggleButtonText => IsSessionActive ? "⏹ Stop Management Session" : "▶ Start Management Session";
    public string SessionToggleButtonToolTip => IsSessionActive 
        ? "Stop session: Disconnects pyinsteon and restores openHAB Insteon Thing back to ONLINE." 
        : "Start session: Pauses openHAB Insteon Thing and connects pyinsteon to USB PLM for management.";
    public AsyncRelayCommand QueryAldbCommand { get; }
    public AsyncRelayCommand ForceRestoreOpenhabCommand { get; }
    public AsyncRelayCommand CheckConnectionCommand { get; }
    public RelayCommand ResetLayoutCommand { get; }
    public RelayCommand SpreadLayoutCommand { get; }
    public RelayCommand ClearLogsCommand { get; }
    public RelayCommand StageSampleChangeCommand { get; }
    public AsyncRelayCommand CommitStagedChangesCommand { get; }
    public RelayCommand DiscardStagedChangesCommand { get; }
    public RelayCommand RemoveStagedChangeCommand { get; }
    public RelayCommand StageReciprocalLinkCommand { get; }
    public RelayCommand StageDeleteLinkCommand { get; }
    public RelayCommand StageDeviceReplaceCommand { get; }

    public RelayCommand ZoomInCommand { get; }
    public RelayCommand ZoomOutCommand { get; }
    public RelayCommand ResetZoomCommand { get; }
    public RelayCommand SelectAllDevicesCommand { get; }
    public RelayCommand DeselectAllDevicesCommand { get; }
    public RelayCommand SelectOnlyLineDevicesCommand { get; }

    public RelayCommand ShowAllMapNodesCommand { get; }
    public RelayCommand ClearAllMapNodesCommand { get; }
    public RelayCommand SelectLineOnlyMapNodesCommand { get; }

    public AsyncRelayCommand RefreshScenesCommand { get; }
    public AsyncRelayCommand TriggerSceneOnCommand { get; }
    public AsyncRelayCommand TriggerSceneOffCommand { get; }
    public AsyncRelayCommand TriggerSceneFastOnCommand { get; }
    public AsyncRelayCommand TriggerSceneFastOffCommand { get; }
    public AsyncRelayCommand PurgeSelectedGhostTargetCommand { get; }
    public AsyncRelayCommand ExecuteDeviceReplaceCommand { get; }
    public AsyncRelayCommand CreateReciprocalLinkCommand { get; }
    public AsyncRelayCommand DeleteReciprocalLinkCommand { get; }
    public AsyncRelayCommand<OrganizedAldbEntry> DeleteLinkFromEntryCommand { get; }
    public RelayCommand SetSelectedNodeAsControllerCommand { get; }
    public RelayCommand SetSelectedNodeAsResponderCommand { get; }
    public RelayCommand SetSelectedNodeAsReplaceOldCommand { get; }
    public RelayCommand InspectNodeInAldbCommand { get; }
    public RelayCommand CopyNodeAddressCommand { get; }
    public RelayCommand SwapNewLinkAddressesCommand { get; }
    public RelayCommand SwapDeleteLinkAddressesCommand { get; }
    public RelayCommand ApplySelectedGraphNodeCommand { get; }

    public AsyncRelayCommand AuditDefaultLinksCommand { get; }
    public RelayCommand StageDefaultLinkForDeviceCommand { get; }
    public AsyncRelayCommand<object> FixDefaultLinkForDeviceCommand { get; }
    public RelayCommand StageFixAllDefaultLinksCommand { get; }
    public RelayCommand StageAddDeviceCommand { get; }
    public AsyncRelayCommand AddDeviceNowCommand { get; }

    public AsyncRelayCommand AnalyzeNetworkCommand { get; }
    public RelayCommand<string> FilterAnalyzerCommand { get; }
    public AsyncRelayCommand<NetworkIssueItemModel> FixNetworkIssueCommand { get; }
    public AsyncRelayCommand<NetworkIssueModel> DeleteNetworkIssueSlotCommand { get; }
    public RelayCommand<NetworkIssueModel> StageNetworkIssueFixCommand { get; }
    public AsyncRelayCommand FixAllHalfLinksCommand { get; }
    public RelayCommand StageAllAnalyzerFixesCommand { get; }

    public ObservableCollection<DeviceLookupItem> DeviceLookupChoices { get; } = new();

    private int _selectedTabIndex = 0;
    public int SelectedTabIndex
    {
        get => _selectedTabIndex;
        set => SetProperty(ref _selectedTabIndex, value);
    }

    public AsyncRelayCommand<string> ControlDeviceCommand { get; }
    public AsyncRelayCommand<string> ControlSelectedNodeCommand { get; }
    public AsyncRelayCommand<string> QuickTriggerSceneCommand { get; }
    public RelayCommand SelectGraphNodeCommand { get; }

    public ObservableCollection<SceneModel> Scenes { get; }
    public ObservableCollection<SceneModel> FilteredScenes { get; }
    public ObservableCollection<string> GhostTargets { get; }
    public ObservableCollection<MissingDefaultLinkItemModel> MissingDefaultLinks { get; }
    public ObservableCollection<DeviceCategoryOption> DeviceCategoryOptions { get; }

    public ObservableCollection<NetworkIssueItemModel> NetworkIssues { get; } = new();
    public ObservableCollection<NetworkIssueItemModel> FilteredNetworkIssues { get; } = new();

    private int _networkHealthScore = 100;
    public int NetworkHealthScore
    {
        get => _networkHealthScore;
        set
        {
            if (SetProperty(ref _networkHealthScore, value))
            {
                OnPropertyChanged(nameof(NetworkHealthStatus));
                OnPropertyChanged(nameof(NetworkHealthColor));
                OnPropertyChanged(nameof(NetworkHealthSummaryText));
            }
        }
    }

    public string NetworkHealthStatus => NetworkHealthScore switch
    {
        >= 90 => "Excellent",
        >= 75 => "Good",
        >= 50 => "Fair - Needs Maintenance",
        _ => "Critical - Severe Issues"
    };

    public string NetworkHealthColor => NetworkHealthScore switch
    {
        >= 90 => "#A6E3A1",
        >= 75 => "#89B4FA",
        >= 50 => "#F9E2AF",
        _ => "#F38BA8"
    };

    public string NetworkHealthSummaryText => $"{NetworkHealthScore}% Health • {AnalyzerTotalIssuesCount} Issues Identified";

    private int _analyzerTotalIssuesCount;
    public int AnalyzerTotalIssuesCount
    {
        get => _analyzerTotalIssuesCount;
        set => SetProperty(ref _analyzerTotalIssuesCount, value);
    }

    private int _analyzerHalfLinksCount;
    public int AnalyzerHalfLinksCount
    {
        get => _analyzerHalfLinksCount;
        set => SetProperty(ref _analyzerHalfLinksCount, value);
    }

    private int _analyzerLoopbacksCount;
    public int AnalyzerLoopbacksCount
    {
        get => _analyzerLoopbacksCount;
        set => SetProperty(ref _analyzerLoopbacksCount, value);
    }

    private int _analyzerMissingPlmCount;
    public int AnalyzerMissingPlmCount
    {
        get => _analyzerMissingPlmCount;
        set => SetProperty(ref _analyzerMissingPlmCount, value);
    }

    private int _analyzerGhostCount;
    public int AnalyzerGhostCount
    {
        get => _analyzerGhostCount;
        set => SetProperty(ref _analyzerGhostCount, value);
    }

    private string _selectedAnalyzerFilter = "All";
    public string SelectedAnalyzerFilter
    {
        get => _selectedAnalyzerFilter;
        set
        {
            if (SetProperty(ref _selectedAnalyzerFilter, value))
            {
                ApplyAnalyzerFilter();
            }
        }
    }

    private bool _isAnalyzingNetwork;
    public bool IsAnalyzingNetwork
    {
        get => _isAnalyzingNetwork;
        set => SetProperty(ref _isAnalyzingNetwork, value);
    }

    public int MissingDefaultLinksCount => MissingDefaultLinks.Count;
    public bool HasMissingDefaultLinks => MissingDefaultLinks.Count > 0;
    public string DefaultLinkAuditStatusText => HasMissingDefaultLinks
        ? $"⚠️ {MissingDefaultLinks.Count} devices missing default PLM links"
        : "✅ All devices have default PLM links";

    public string PlmModemAddress => "53.C1.42";

    private string _newDeviceAddress = string.Empty;
    public string NewDeviceAddress
    {
        get => _newDeviceAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _newDeviceAddress, normalized))
            {
                OnPropertyChanged(nameof(NewDeviceValidation));
                StageAddDeviceCommand?.NotifyCanExecuteChanged();
                AddDeviceNowCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private string _newDeviceName = string.Empty;
    public string NewDeviceName
    {
        get => _newDeviceName;
        set => SetProperty(ref _newDeviceName, value);
    }

    private DeviceCategoryOption? _selectedDeviceCategory;
    public DeviceCategoryOption? SelectedDeviceCategory
    {
        get => _selectedDeviceCategory;
        set => SetProperty(ref _selectedDeviceCategory, value);
    }

    private bool _newDeviceCreateDefaultLinks = true;
    public bool NewDeviceCreateDefaultLinks
    {
        get => _newDeviceCreateDefaultLinks;
        set => SetProperty(ref _newDeviceCreateDefaultLinks, value);
    }

    public string NewDeviceValidation => InsteonAddressHelper.IsValid(NewDeviceAddress)
        ? "✓ Valid Insteon Address"
        : (string.IsNullOrEmpty(NewDeviceAddress) ? "" : "⚠ Invalid address (format: XX.YY.ZZ)");

    private DeviceNode? _selectedGraphNode;
    public DeviceNode? SelectedGraphNode
    {
        get => _selectedGraphNode;
        set
        {
            if (SetProperty(ref _selectedGraphNode, value))
            {
                foreach (var n in DeviceNodes)
                {
                    n.IsSelected = (n == value);
                }
                OnPropertyChanged(nameof(HasSelectedGraphNode));
                OnPropertyChanged(nameof(SelectedGraphNodeTitle));
            }
        }
    }

    public bool HasSelectedGraphNode => SelectedGraphNode != null;

    public string SelectedGraphNodeTitle => SelectedGraphNode != null
        ? $"{SelectedGraphNode.FriendlyName} ({SelectedGraphNode.Address})"
        : "No Device Selected";

    private int _selectedNodeBrightness = 100;
    public int SelectedNodeBrightness
    {
        get => _selectedNodeBrightness;
        set => SetProperty(ref _selectedNodeBrightness, Math.Clamp(value, 0, 100));
    }

    private SceneModel? _selectedQuickScene;
    public SceneModel? SelectedQuickScene
    {
        get => _selectedQuickScene;
        set => SetProperty(ref _selectedQuickScene, value);
    }

    private SceneModel? _selectedScene;
    public SceneModel? SelectedScene
    {
        get => _selectedScene;
        set => SetProperty(ref _selectedScene, value);
    }

    private string _sceneFilterText = string.Empty;
    public string SceneFilterText
    {
        get => _sceneFilterText;
        set
        {
            if (SetProperty(ref _sceneFilterText, value))
            {
                ApplySceneFilter();
            }
        }
    }

    private string? _selectedGhostTarget;
    public string? SelectedGhostTarget
    {
        get => _selectedGhostTarget;
        set => SetProperty(ref _selectedGhostTarget, value);
    }

    private DeviceLookupItem? EnsureLookupChoice(string normalizedAddress)
    {
        if (string.IsNullOrWhiteSpace(normalizedAddress)) return null;
        var match = DeviceLookupChoices.FirstOrDefault(d => d.Address.Equals(normalizedAddress, StringComparison.OrdinalIgnoreCase));
        if (match == null && InsteonAddressHelper.IsValid(normalizedAddress))
        {
            match = new DeviceLookupItem
            {
                Address = normalizedAddress,
                FriendlyName = $"Device {normalizedAddress}",
                DeviceType = "Custom"
            };
            DeviceLookupChoices.Add(match);
        }
        return match;
    }

    private string _replaceOldDeviceAddress = string.Empty;
    public string ReplaceOldDeviceAddress
    {
        get => _replaceOldDeviceAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _replaceOldDeviceAddress, normalized))
            {
                _selectedReplaceOldDevice = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedReplaceOldDevice));
                OnPropertyChanged(nameof(ReplaceOldDeviceValidation));
                ExecuteDeviceReplaceCommand?.NotifyCanExecuteChanged();
                StageDeviceReplaceCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedReplaceOldDevice;
    public DeviceLookupItem? SelectedReplaceOldDevice
    {
        get => _selectedReplaceOldDevice;
        set
        {
            if (SetProperty(ref _selectedReplaceOldDevice, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(ReplaceOldDeviceAddress, StringComparison.OrdinalIgnoreCase))
                {
                    ReplaceOldDeviceAddress = value.Address;
                }
            }
        }
    }

    public string ReplaceOldDeviceValidation => GetAddressValidationBadge(ReplaceOldDeviceAddress);

    private string _replaceNewDeviceAddress = string.Empty;
    public string ReplaceNewDeviceAddress
    {
        get => _replaceNewDeviceAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _replaceNewDeviceAddress, normalized))
            {
                _selectedReplaceNewDevice = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedReplaceNewDevice));
                OnPropertyChanged(nameof(ReplaceNewDeviceValidation));
                ExecuteDeviceReplaceCommand?.NotifyCanExecuteChanged();
                StageDeviceReplaceCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedReplaceNewDevice;
    public DeviceLookupItem? SelectedReplaceNewDevice
    {
        get => _selectedReplaceNewDevice;
        set
        {
            if (SetProperty(ref _selectedReplaceNewDevice, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(ReplaceNewDeviceAddress, StringComparison.OrdinalIgnoreCase))
                {
                    ReplaceNewDeviceAddress = value.Address;
                }
            }
        }
    }

    public string ReplaceNewDeviceValidation => GetAddressValidationBadge(ReplaceNewDeviceAddress);

    private bool _replaceCopyLinks = true;
    public bool ReplaceCopyLinks
    {
        get => _replaceCopyLinks;
        set => SetProperty(ref _replaceCopyLinks, value);
    }

    private string _newLinkControllerAddress = string.Empty;
    public string NewLinkControllerAddress
    {
        get => _newLinkControllerAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _newLinkControllerAddress, normalized))
            {
                _selectedNewLinkController = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedNewLinkController));
                OnPropertyChanged(nameof(NewLinkControllerValidation));
                CreateReciprocalLinkCommand?.NotifyCanExecuteChanged();
                StageReciprocalLinkCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedNewLinkController;
    public DeviceLookupItem? SelectedNewLinkController
    {
        get => _selectedNewLinkController;
        set
        {
            if (SetProperty(ref _selectedNewLinkController, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(NewLinkControllerAddress, StringComparison.OrdinalIgnoreCase))
                {
                    NewLinkControllerAddress = value.Address;
                }
            }
        }
    }

    public string NewLinkControllerValidation => GetAddressValidationBadge(NewLinkControllerAddress);

    private string _newLinkResponderAddress = string.Empty;
    public string NewLinkResponderAddress
    {
        get => _newLinkResponderAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _newLinkResponderAddress, normalized))
            {
                _selectedNewLinkResponder = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedNewLinkResponder));
                OnPropertyChanged(nameof(NewLinkResponderValidation));
                CreateReciprocalLinkCommand?.NotifyCanExecuteChanged();
                StageReciprocalLinkCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedNewLinkResponder;
    public DeviceLookupItem? SelectedNewLinkResponder
    {
        get => _selectedNewLinkResponder;
        set
        {
            if (SetProperty(ref _selectedNewLinkResponder, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(NewLinkResponderAddress, StringComparison.OrdinalIgnoreCase))
                {
                    NewLinkResponderAddress = value.Address;
                }
            }
        }
    }

    public string NewLinkResponderValidation => GetAddressValidationBadge(NewLinkResponderAddress);

    private int _newLinkGroup = 1;
    public int NewLinkGroup
    {
        get => _newLinkGroup;
        set => SetProperty(ref _newLinkGroup, value);
    }

    private int _newLinkOnLevel = 255;
    public int NewLinkOnLevel
    {
        get => _newLinkOnLevel;
        set => SetProperty(ref _newLinkOnLevel, value);
    }

    private int _newLinkRampRate = 28;
    public int NewLinkRampRate
    {
        get => _newLinkRampRate;
        set => SetProperty(ref _newLinkRampRate, value);
    }

    private string _deleteLinkControllerAddress = string.Empty;
    public string DeleteLinkControllerAddress
    {
        get => _deleteLinkControllerAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _deleteLinkControllerAddress, normalized))
            {
                _selectedDeleteLinkController = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedDeleteLinkController));
                OnPropertyChanged(nameof(DeleteLinkControllerValidation));
                DeleteReciprocalLinkCommand?.NotifyCanExecuteChanged();
                StageDeleteLinkCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedDeleteLinkController;
    public DeviceLookupItem? SelectedDeleteLinkController
    {
        get => _selectedDeleteLinkController;
        set
        {
            if (SetProperty(ref _selectedDeleteLinkController, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(DeleteLinkControllerAddress, StringComparison.OrdinalIgnoreCase))
                {
                    DeleteLinkControllerAddress = value.Address;
                }
            }
        }
    }

    public string DeleteLinkControllerValidation => GetAddressValidationBadge(DeleteLinkControllerAddress);

    private string _deleteLinkResponderAddress = string.Empty;
    public string DeleteLinkResponderAddress
    {
        get => _deleteLinkResponderAddress;
        set
        {
            var normalized = InsteonAddressHelper.Normalize(value);
            if (SetProperty(ref _deleteLinkResponderAddress, normalized))
            {
                _selectedDeleteLinkResponder = EnsureLookupChoice(normalized);
                OnPropertyChanged(nameof(SelectedDeleteLinkResponder));
                OnPropertyChanged(nameof(DeleteLinkResponderValidation));
                DeleteReciprocalLinkCommand?.NotifyCanExecuteChanged();
                StageDeleteLinkCommand?.NotifyCanExecuteChanged();
            }
        }
    }

    private DeviceLookupItem? _selectedDeleteLinkResponder;
    public DeviceLookupItem? SelectedDeleteLinkResponder
    {
        get => _selectedDeleteLinkResponder;
        set
        {
            if (SetProperty(ref _selectedDeleteLinkResponder, value))
            {
                if (value != null && !string.IsNullOrWhiteSpace(value.Address) && !value.Address.Equals(DeleteLinkResponderAddress, StringComparison.OrdinalIgnoreCase))
                {
                    DeleteLinkResponderAddress = value.Address;
                }
            }
        }
    }

    public string DeleteLinkResponderValidation => GetAddressValidationBadge(DeleteLinkResponderAddress);

    private string GetAddressValidationBadge(string address)
    {
        if (string.IsNullOrWhiteSpace(address)) return string.Empty;
        var match = DeviceLookupChoices.FirstOrDefault(d => d.Address.Equals(address, StringComparison.OrdinalIgnoreCase));
        if (match != null) return $"✓ {match.FriendlyName} ({match.RecordCount} records)";
        if (InsteonAddressHelper.IsValid(address)) return "✓ Valid Address (Unlinked/New)";
        return "⚠ Invalid format (e.g. 1A.2B.3C)";
    }

    private int _deleteLinkGroup = 1;
    public int DeleteLinkGroup
    {
        get => _deleteLinkGroup;
        set => SetProperty(ref _deleteLinkGroup, value);
    }

    public ObservableCollection<ActiveLinkPairModel> ActiveLinkPairs { get; }

    private ActiveLinkPairModel? _selectedActiveLink;
    public ActiveLinkPairModel? SelectedActiveLink
    {
        get => _selectedActiveLink;
        set
        {
            if (SetProperty(ref _selectedActiveLink, value) && value != null)
            {
                DeleteLinkControllerAddress = value.ControllerAddress;
                DeleteLinkResponderAddress = value.ResponderAddress;
                DeleteLinkGroup = value.Group;
            }
        }
    }

    public void AddLog(LogLevel level, string source, string message)
    {
        var entry = new LogEntryModel
        {
            Timestamp = DateTime.Now,
            Level = level,
            Source = source,
            Message = message
        };

        LogEntries.Insert(0, entry);

        // Keep maximum 500 recent entries
        while (LogEntries.Count > 500)
        {
            LogEntries.RemoveAt(LogEntries.Count - 1);
        }
    }

    public void ClearLogs()
    {
        LogEntries.Clear();
        AddLog(LogLevel.Info, "System", "Log cleared.");
    }

    private void UpdateBackendUrl()
    {
        _apiClient.SetBaseUrl(_backendUrl);
    }

    public async Task CheckStatusAsync()
    {
        var status = await _apiClient.GetSessionStatusAsync();
        if (status != null)
        {
            IsSessionActive = status.IsActive && status.LockedBy == _clientId;
            RemainingLeaseSeconds = status.LeaseExpiresInSeconds ?? 0;

            if (status.IsActive)
            {
                SessionStatusText = $"Session: ACTIVE ({status.LeaseExpiresInSeconds}s)";
            }
            else
            {
                SessionStatusText = "Session: INACTIVE (Port Free)";
            }

            OpenhabStatusText = status.OpenhabThingEnabled
                ? "openHAB: ONLINE"
                : "openHAB: PAUSED (Released for GUI)";
        }
        else
        {
            SessionStatusText = "Backend Offline";
            OpenhabStatusText = "openHAB: Unknown";
        }

        OnPropertyChanged(nameof(SessionToggleButtonText));
        OnPropertyChanged(nameof(SessionToggleButtonToolTip));
    }

    private async Task ToggleSessionAsync()
    {
        if (IsSessionActive)
        {
            await EndSessionAsync();
        }
        else
        {
            await StartSessionAsync();
        }
    }

    public async Task ReleaseHardwareOnExitAsync()
    {
        try
        {
            _heartbeatTimer.Stop();
            var releaseTask = _apiClient.ReleaseSessionAsync(_clientId);
            // Wait up to 3 seconds for clean backend acknowledgement
            await Task.WhenAny(releaseTask, Task.Delay(3000));
            IsSessionActive = false;
            OnPropertyChanged(nameof(SessionToggleButtonText));
            OnPropertyChanged(nameof(SessionToggleButtonToolTip));
        }
        catch
        {
            // Ignore during process termination
        }
    }

    private async Task StartSessionAsync()
    {
        IsLoading = true;
        StatusMessage = "Acquiring lock: Disabling openHAB Insteon Thing and connecting pyinsteon...";
        AddLog(LogLevel.Info, "SessionManager", "Requesting hardware lock from backend...");
        try
        {
            var (success, message) = await _apiClient.AcquireSessionAsync(_clientId);
            if (success)
            {
                IsSessionActive = true;
                _heartbeatTimer.Start();
                StatusMessage = "Session active. openHAB paused, pyinsteon connected.";
                AddLog(LogLevel.Success, "SessionManager", "Hardware lock acquired. pyinsteon connected to USB PLM.");
                await CheckStatusAsync();
            }
            else
            {
                StatusMessage = $"Failed to acquire session: {message}";
                AddLog(LogLevel.Error, "SessionManager", $"Session acquisition failed: {message}");
            }
        }
        catch (Exception ex)
        {
            AddLog(LogLevel.Error, "SessionManager", $"Error: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    private async Task EndSessionAsync()
    {
        IsLoading = true;
        StatusMessage = "Releasing lock: Disconnecting pyinsteon and re-enabling openHAB...";
        AddLog(LogLevel.Info, "SessionManager", "Releasing hardware lock and restoring openHAB...");
        try
        {
            _heartbeatTimer.Stop();
            var (success, message) = await _apiClient.ReleaseSessionAsync(_clientId);
            if (success)
            {
                IsSessionActive = false;
                StatusMessage = "Session released. openHAB Insteon Thing restored.";
                AddLog(LogLevel.Success, "SessionManager", "Hardware released. openHAB Insteon Thing restored to ONLINE.");
                await CheckStatusAsync();
            }
            else
            {
                StatusMessage = $"Failed to release session: {message}";
                AddLog(LogLevel.Warning, "SessionManager", $"Session release warning: {message}");
            }
        }
        catch (Exception ex)
        {
            AddLog(LogLevel.Error, "SessionManager", $"Error: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    private async Task OnHeartbeatTickAsync()
    {
        if (IsSessionActive)
        {
            var renewed = await _apiClient.SendHeartbeatAsync(_clientId);
            if (renewed)
            {
                AddLog(LogLevel.Info, "Watchdog", "Heartbeat acknowledged. 300s lease extended.");
            }
            else
            {
                StatusMessage = "Warning: Failed to renew session lease with backend.";
                AddLog(LogLevel.Warning, "Watchdog", "Heartbeat rejected or backend unreachable.");
            }
        }
    }

    public async Task QueryAldbAsync(bool silent = false)
    {
        if (ForceRefreshHardware && !IsSessionActive)
        {
            StatusMessage = "Must start session before reading ALDB directly from hardware.";
            AddLog(LogLevel.Warning, "InsteonAPI", "Blocked query attempt: Session is not active for hardware read.");
            return;
        }

        IsLoading = true;
        if (!silent)
        {
            StatusMessage = ForceRefreshHardware
                ? "Reading ALDB directly from hardware (pacing powerline queries)..."
                : "Querying ALDB records from backend cache...";

            AddLog(LogLevel.Info, "InsteonAPI", ForceRefreshHardware ? "Querying ALDB from wire hardware..." : "Querying ALDB from backend cache...");
        }

        try
        {
            var recordsTask = _apiClient.GetPlmAldbAsync(ForceRefreshHardware);
            var devicesTask = _apiClient.GetDevicesAsync();
            await Task.WhenAll(recordsTask, devicesTask);

            var records = await recordsTask;
            _currentDevices = await devicesTask;

            AldbRecords.Clear();
            foreach (var rec in records)
            {
                AldbRecords.Add(rec);
            }

            ApplyFilter();
            BuildTopologyGraph(records, _currentDevices);
            AnalyzeGhostLinks(records);
            RebuildActiveLinkPairs(records);
            await LoadScenesAsync(silent: silent);
            await AuditDefaultLinksAsync(silent: silent);
            UpdateSelectableDevices(_currentDevices, records);
            await RunNetworkAnalysisAsync(silent: silent);

            if (!silent)
            {
                StatusMessage = $"Loaded {records.Count} ALDB records successfully. Generated {TopologyLinks.Count} topology link relations across {Scenes.Count} scenes.";
                AddLog(LogLevel.Success, "InsteonAPI", $"Retrieved {records.Count} ALDB records. Synthesized {TopologyLinks.Count} directed link relationships across {_currentDevices.Count} devices and {Scenes.Count} scenes.");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"ALDB query failed: {ex.Message}";
            AddLog(LogLevel.Error, "InsteonAPI", $"ALDB query failed: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void ApplyFilter()
    {
        FilteredAldbRecords.Clear();
        var query = FilterText?.Trim();

        foreach (var rec in AldbRecords)
        {
            if (string.IsNullOrWhiteSpace(query) ||
                rec.TargetAddress.Contains(query, StringComparison.OrdinalIgnoreCase) ||
                rec.MemAddr.Contains(query, StringComparison.OrdinalIgnoreCase) ||
                rec.Group.ToString().Contains(query) ||
                rec.RawMode.Contains(query, StringComparison.OrdinalIgnoreCase))
            {
                FilteredAldbRecords.Add(rec);
            }
        }
    }

    public void SaveNodePositions()
    {
        _positionService.SavePositions(DeviceNodes);
        AddLog(LogLevel.Info, "Topology", "Node canvas positions persisted to local storage.");
    }

    public void ResetLayout()
    {
        if (AldbRecords.Count > 0)
        {
            BuildTopologyGraph(AldbRecords.ToList(), _currentDevices, ignoreSavedPositions: true);
            SaveNodePositions();
            StatusMessage = "Topology layout reset to default circle layout.";
            AddLog(LogLevel.Info, "Topology", "Reset layout to default circle arrangement.");
        }
    }

    public void SpreadLayout()
    {
        if (AldbRecords.Count > 0)
        {
            BuildTopologyGraph(AldbRecords.ToList(), _currentDevices, ignoreSavedPositions: true);
            SaveNodePositions();
            StatusMessage = $"Expanded network topology layout to {_spreadDistance:0}px spacing.";
            AddLog(LogLevel.Info, "Topology", $"Expanded network layout to {_spreadDistance:0}px spacing without overlapping.");
        }
    }

    public void ZoomIn()
    {
        ZoomLevel = Math.Round(_zoomLevel + 0.15, 2);
    }

    public void ZoomOut()
    {
        ZoomLevel = Math.Round(_zoomLevel - 0.15, 2);
    }

    public void ResetZoom()
    {
        ZoomLevel = 1.0;
    }

    public void SelectAllDevices(bool select)
    {
        foreach (var dev in SelectableDevices)
        {
            dev.IsSelected = select;
        }
        RebuildOrganizedAldbView();
    }

    public void SelectOnlyLineDevices()
    {
        foreach (var dev in SelectableDevices)
        {
            dev.IsSelected = !dev.IsBattery;
        }
        RebuildOrganizedAldbView();
    }

    public void ShowAllMapNodes()
    {
        foreach (var node in DeviceNodes)
        {
            node.IsFilterChecked = true;
        }
        UpdateMapVisibility();
    }

    public void ClearAllMapNodes()
    {
        foreach (var node in DeviceNodes)
        {
            node.IsFilterChecked = false;
        }
        UpdateMapVisibility();
    }

    public void SelectLineOnlyMapNodes()
    {
        foreach (var node in DeviceNodes)
        {
            node.IsFilterChecked = !node.IsBattery || node.IsModem;
        }
        UpdateMapVisibility();
    }

    private void ApplyMapNodeSearchFilter()
    {
        FilteredMapNodes.Clear();
        var q = MapNodeFilterText?.Trim() ?? string.Empty;
        foreach (var node in DeviceNodes)
        {
            if (string.IsNullOrWhiteSpace(q) ||
                node.Address.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                node.FriendlyName.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                node.Label.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                node.DeviceType.Contains(q, StringComparison.OrdinalIgnoreCase))
            {
                FilteredMapNodes.Add(node);
            }
        }
    }

    public void UpdateMapVisibility()
    {
        if (DeviceNodes.Count == 0)
        {
            VisibleNodesCount = 0;
            VisibleLinksCount = 0;
            OnPropertyChanged(nameof(IsMapFiltered));
            OnPropertyChanged(nameof(MapFilterStatusText));
            return;
        }

        var checkedNodes = DeviceNodes.Where(n => n.IsFilterChecked).ToHashSet();
        var visibleSet = new HashSet<DeviceNode>(checkedNodes);

        if (IncludeConnectedPeers && checkedNodes.Count > 0)
        {
            foreach (var link in TopologyLinks)
            {
                if (checkedNodes.Contains(link.SourceNode))
                {
                    visibleSet.Add(link.TargetNode);
                }
                if (checkedNodes.Contains(link.TargetNode))
                {
                    visibleSet.Add(link.SourceNode);
                }
            }
        }

        foreach (var node in DeviceNodes)
        {
            node.IsVisible = visibleSet.Contains(node);
        }

        foreach (var link in TopologyLinks)
        {
            link.IsVisible = link.SourceNode.IsVisible && link.TargetNode.IsVisible;
        }

        VisibleNodesCount = DeviceNodes.Count(n => n.IsVisible);
        VisibleLinksCount = TopologyLinks.Count(l => l.IsVisible);

        OnPropertyChanged(nameof(IsMapFiltered));
        OnPropertyChanged(nameof(MapFilterStatusText));
    }

    private void ApplyDeviceFilter()
    {
        FilteredSelectableDevices.Clear();
        var q = DeviceFilterText?.Trim() ?? string.Empty;
        foreach (var dev in SelectableDevices)
        {
            if (string.IsNullOrWhiteSpace(q) ||
                dev.Address.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                dev.FriendlyName.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                dev.DeviceType.Contains(q, StringComparison.OrdinalIgnoreCase))
            {
                FilteredSelectableDevices.Add(dev);
            }
        }
    }

    private void UpdateSelectableDevices(List<DeviceSummaryModel> devices, List<AldbRecordModel> records)
    {
        SelectableDevices.Clear();

        var devMap = devices?.ToDictionary(d => d.Address, StringComparer.OrdinalIgnoreCase) ?? new();
        var allAddresses = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        if (devices != null)
        {
            foreach (var d in devices)
            {
                allAddresses.Add(d.Address);
            }
        }

        foreach (var r in records)
        {
            if (!string.IsNullOrWhiteSpace(r.SourceAddress))
                allAddresses.Add(r.SourceAddress);
        }

        var sorted = allAddresses.OrderBy(a => a).ToList();

        foreach (var addr in sorted)
        {
            devMap.TryGetValue(addr, out var summary);
            string name = summary?.Description ?? $"Device {addr}";
            bool isBatt = summary?.IsBattery ?? false;
            string devType = summary?.Description ?? "Line Device";
            int count = records.Count(r => r.InUse && (r.SourceAddress == addr || (addr == "PLM Modem" && r.SourceAddress == "53.C1.42")));

            var item = new SelectableDeviceItem
            {
                Address = addr,
                FriendlyName = name,
                DeviceType = devType,
                RecordCount = count,
                IsBattery = isBatt,
                // Default select line devices with active records
                IsSelected = count > 0 && !isBatt,
            };

            item.PropertyChanged += (s, e) =>
            {
                if (e.PropertyName == nameof(SelectableDeviceItem.IsSelected))
                {
                    RebuildOrganizedAldbView();
                }
            };

            SelectableDevices.Add(item);
        }

        ApplyDeviceFilter();
        RebuildOrganizedAldbView();
        RebuildDeviceLookupChoices();
    }

    private void RebuildDeviceLookupChoices()
    {
        DeviceLookupChoices.Clear();
        var items = SelectableDevices
            .Select(d => new DeviceLookupItem
            {
                Address = d.Address,
                FriendlyName = d.FriendlyName,
                DeviceType = d.DeviceType,
                RecordCount = d.RecordCount,
                IsBattery = d.IsBattery,
                IsModem = d.Address.Equals("53.C1.42", StringComparison.OrdinalIgnoreCase) || d.FriendlyName.Contains("Hub", StringComparison.OrdinalIgnoreCase) || d.FriendlyName.Contains("PLM", StringComparison.OrdinalIgnoreCase),
            })
            .OrderBy(d => d.FriendlyName)
            .ToList();

        foreach (var item in items)
        {
            DeviceLookupChoices.Add(item);
        }

        // Restore staged selections if addresses are already present
        if (!string.IsNullOrEmpty(NewLinkControllerAddress))
        {
            _selectedNewLinkController = EnsureLookupChoice(NewLinkControllerAddress);
            OnPropertyChanged(nameof(SelectedNewLinkController));
        }
        if (!string.IsNullOrEmpty(NewLinkResponderAddress))
        {
            _selectedNewLinkResponder = EnsureLookupChoice(NewLinkResponderAddress);
            OnPropertyChanged(nameof(SelectedNewLinkResponder));
        }
        if (!string.IsNullOrEmpty(DeleteLinkControllerAddress))
        {
            _selectedDeleteLinkController = EnsureLookupChoice(DeleteLinkControllerAddress);
            OnPropertyChanged(nameof(SelectedDeleteLinkController));
        }
        if (!string.IsNullOrEmpty(DeleteLinkResponderAddress))
        {
            _selectedDeleteLinkResponder = EnsureLookupChoice(DeleteLinkResponderAddress);
            OnPropertyChanged(nameof(SelectedDeleteLinkResponder));
        }
        if (!string.IsNullOrEmpty(ReplaceOldDeviceAddress))
        {
            _selectedReplaceOldDevice = EnsureLookupChoice(ReplaceOldDeviceAddress);
            OnPropertyChanged(nameof(SelectedReplaceOldDevice));
        }
        if (!string.IsNullOrEmpty(ReplaceNewDeviceAddress))
        {
            _selectedReplaceNewDevice = EnsureLookupChoice(ReplaceNewDeviceAddress);
            OnPropertyChanged(nameof(SelectedReplaceNewDevice));
        }
    }

    private void RebuildOrganizedAldbView()
    {
        OrganizedDevices.Clear();
        var selected = SelectableDevices.Where(d => d.IsSelected).ToList();
        SelectedDevicesCount = selected.Count;

        var friendlyNameMap = SelectableDevices.ToDictionary(d => d.Address, d => d.FriendlyName, StringComparer.OrdinalIgnoreCase);
        string search = AldbSearchText?.Trim() ?? string.Empty;

        foreach (var dev in selected)
        {
            var missing = MissingDefaultLinks.FirstOrDefault(m => m.DeviceAddress.Equals(dev.Address, StringComparison.OrdinalIgnoreCase));

            var org = new OrganizedDeviceAldb
            {
                Address = dev.Address,
                FriendlyName = dev.FriendlyName,
                DeviceType = dev.DeviceType,
                IsBattery = dev.IsBattery,
                IsModem = dev.Address == "53.C1.42" || dev.Address.Contains("PLM", StringComparison.OrdinalIgnoreCase),
                HasMissingDefaultLink = (missing != null),
                DefaultLinkIssue = missing?.IssueSummary ?? string.Empty,
                DefaultLinkRecommendation = missing?.Recommendation ?? string.Empty,
            };

            // Get records owned by this device
            var devRecords = AldbRecords.Where(r => 
                (r.SourceAddress != null && r.SourceAddress.Equals(dev.Address, StringComparison.OrdinalIgnoreCase)) ||
                (org.IsModem && (r.SourceAddress == "PLM Modem" || r.SourceAddress == "53.C1.42"))
            ).ToList();

            // Populate Keypad button mapping if device is a Keypad / Remote
            if (org.IsKeypad)
            {
                var keypadButtons = InsteonKeypadHelper.BuildKeypadButtons(dev.Address, devRecords, friendlyNameMap);
                foreach (var btn in keypadButtons)
                {
                    org.KeypadButtons.Add(btn);
                }
            }

            foreach (var r in devRecords)
            {
                string tgtName = friendlyNameMap.TryGetValue(r.TargetAddress, out var fn) ? fn : $"Device {r.TargetAddress}";
                string decoded = DecodeInsteonRecord(r, tgtName);
                string btnLabel = org.IsKeypad ? InsteonKeypadHelper.GetKeypadButtonShortLabel(r.Group, r.Data3) : string.Empty;

                // If search query is present, check match
                if (!string.IsNullOrWhiteSpace(search))
                {
                    bool matches = r.MemAddr.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                                   r.TargetAddress.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                                   tgtName.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                                   r.Group.ToString().Contains(search) ||
                                   decoded.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                                   btnLabel.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                                   r.RawMode.Contains(search, StringComparison.OrdinalIgnoreCase);
                    if (!matches) continue;
                }

                var entry = new OrganizedAldbEntry
                {
                    OwnerAddress = dev.Address,
                    OwnerFriendlyName = dev.FriendlyName,
                    MemAddr = r.MemAddr,
                    TargetAddress = r.TargetAddress,
                    TargetFriendlyName = tgtName,
                    Group = r.Group,
                    Role = r.IsController ? "Controller" : (r.IsResponder ? "Responder" : "Inactive"),
                    IsController = r.IsController,
                    IsResponder = r.IsResponder,
                    InUse = r.InUse,
                    HighWaterMark = r.HighWaterMark,
                    Data1 = r.Data1,
                    Data2 = r.Data2,
                    Data3 = r.Data3,
                    DecodedSummary = decoded,
                    StatusBadge = r.HighWaterMark ? "High Water Mark" : (r.InUse ? "Active" : "Deleted"),
                    IsKeypadRecord = org.IsKeypad,
                    KeypadButtonLabel = btnLabel,
                };

                if (r.HighWaterMark || !r.InUse)
                {
                    org.OtherRecords.Add(entry);
                }
                else if (r.IsController)
                {
                    org.ControllerRecords.Add(entry);
                }
                else if (r.IsResponder)
                {
                    org.ResponderRecords.Add(entry);
                }
                else
                {
                    org.OtherRecords.Add(entry);
                }
            }

            // Flag duplicate links and Group 0 anomalies for clear user visibility
            var seenCtrl = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var entry in org.ControllerRecords)
            {
                entry.IsGroupZero = (entry.Group == 0);
                string key = $"{entry.TargetAddress}|{entry.Group}";
                if (!seenCtrl.Add(key))
                {
                    entry.IsDuplicate = true;
                }
            }

            var seenResp = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var entry in org.ResponderRecords)
            {
                entry.IsGroupZero = (entry.Group == 0);
                string key = $"{entry.TargetAddress}|{entry.Group}";
                if (!seenResp.Add(key))
                {
                    entry.IsDuplicate = true;
                }
            }

            OrganizedDevices.Add(org);
        }
    }

    private static string DecodeInsteonRecord(AldbRecordModel r, string targetFriendlyName)
    {
        if (r.HighWaterMark)
        {
            return "End of Database Marker (High-Water Mark)";
        }
        if (!r.InUse)
        {
            return "Unused / Cleared Database Slot";
        }

        if (r.IsResponder)
        {
            string onLevel = r.Data1 == 255 ? "100% (Full On)" : (r.Data1 == 0 ? "Off (0%)" : $"{Math.Round(r.Data1 / 255.0 * 100)}% ({r.Data1})");
            string ramp = DecodeRampRate(r.Data2);
            string btn = r.Data3 > 0 ? $" • Keypad Button {r.Data3}" : "";
            return $"Responds to {targetFriendlyName} on Scene {r.Group} | Level: {onLevel} | Ramp: {ramp}{btn}";
        }
        if (r.IsController)
        {
            string btn = r.Data3 > 0 ? $" (Button {r.Data3})" : "";
            return $"Controls {targetFriendlyName} on Scene / Group {r.Group}{btn}";
        }
        return $"{r.RawMode} link with {targetFriendlyName} (Group {r.Group})";
    }

    private static string DecodeRampRate(int data2)
    {
        return data2 switch
        {
            31 => "0.1s (Instant)",
            30 => "0.17s",
            29 => "0.33s",
            28 => "0.5s",
            27 => "0.67s",
            26 => "0.83s",
            25 => "1.0s",
            24 => "1.2s",
            23 => "1.5s",
            22 => "2.0s",
            21 => "2.5s",
            20 => "3.0s",
            19 => "4.5s",
            18 => "6.0s",
            17 => "9.0s",
            16 => "19s",
            _ => $"{data2} (raw)"
        };
    }

    private void BuildTopologyGraph(List<AldbRecordModel> records, List<DeviceSummaryModel>? devices = null, bool ignoreSavedPositions = false)
    {
        var previousChecked = DeviceNodes.Count > 0
            ? DeviceNodes.Where(n => n.IsFilterChecked).Select(n => n.Address).ToHashSet(StringComparer.OrdinalIgnoreCase)
            : null;

        DeviceNodes.Clear();
        TopologyLinks.Clear();

        var savedPositions = ignoreSavedPositions 
            ? new Dictionary<string, NodeCoordinate>() 
            : _positionService.LoadPositions();

        var deviceMap = devices?.ToDictionary(d => d.Address, StringComparer.OrdinalIgnoreCase) 
            ?? new Dictionary<string, DeviceSummaryModel>(StringComparer.OrdinalIgnoreCase);

        // 1. Identify PLM Modem Hub
        var plmDev = devices?.FirstOrDefault(d => d.Cat == 3 || d.Description.Contains("PLM", StringComparison.OrdinalIgnoreCase) || d.Description.Contains("Modem", StringComparison.OrdinalIgnoreCase));
        string plmAddress = plmDev?.Address ?? "53.C1.42";

        double plmX = 1200;
        double plmY = 900;
        if (savedPositions.TryGetValue("PLM Modem", out var savedPlm) || savedPositions.TryGetValue(plmAddress, out savedPlm))
        {
            plmX = savedPlm.X;
            plmY = savedPlm.Y;
        }

        var plmNode = new DeviceNode
        {
            Address = plmAddress,
            Label = $"PLM Modem ({plmAddress})",
            FriendlyName = "PLM Modem Hub",
            X = plmX,
            Y = plmY,
            IsModem = true,
            IsBattery = false,
            DeviceType = "Hardware Hub / Modem",
            LinkCount = records.Count(r => r.InUse && (r.SourceAddress == plmAddress || r.SourceAddress == "PLM Modem" || (plmAddress == "53.C1.42" && r.SourceAddress == "1A.2B.3C"))),
            IsFilterChecked = previousChecked == null || previousChecked.Contains(plmAddress),
            IsVisible = true,
        };
        plmNode.PropertyChanged += (s, e) =>
        {
            if (e.PropertyName == nameof(DeviceNode.IsFilterChecked))
            {
                UpdateMapVisibility();
            }
        };
        DeviceNodes.Add(plmNode);

        // 2. Distinct peer device addresses in active records + device inventory
        var distinctAddresses = records
            .Where(r => r.InUse)
            .SelectMany(r => new[] { r.TargetAddress, r.SourceAddress })
            .Where(addr => !string.IsNullOrWhiteSpace(addr) && 
                           !addr.Equals("PLM Modem", StringComparison.OrdinalIgnoreCase) && 
                           !addr.Equals("1A.2B.3C", StringComparison.OrdinalIgnoreCase) &&
                           !addr.Equals(plmAddress, StringComparison.OrdinalIgnoreCase))
            .Select(addr => addr!)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        if (devices != null)
        {
            foreach (var d in devices)
            {
                if (!d.Address.Equals(plmAddress, StringComparison.OrdinalIgnoreCase) && 
                    !d.Address.Equals("1A.2B.3C", StringComparison.OrdinalIgnoreCase) &&
                    !distinctAddresses.Contains(d.Address, StringComparer.OrdinalIgnoreCase))
                {
                    distinctAddresses.Add(d.Address);
                }
            }
        }

        if (distinctAddresses.Count == 0)
        {
            ApplyMapNodeSearchFilter();
            UpdateMapVisibility();
            return;
        }

        double centerX = plmX;
        double centerY = plmY;
        double radius = _spreadDistance;
        double angleStep = 2 * Math.PI / distinctAddresses.Count;

        var peerNodeMap = new Dictionary<string, DeviceNode>(StringComparer.OrdinalIgnoreCase);

        for (int i = 0; i < distinctAddresses.Count; i++)
        {
            string address = distinctAddresses[i];
            var ownRecords = records.Where(r => r.InUse && r.SourceAddress != null && r.SourceAddress.Equals(address, StringComparison.OrdinalIgnoreCase)).ToList();
            var peerRecords = records.Where(r => r.InUse && r.TargetAddress.Equals(address, StringComparison.OrdinalIgnoreCase)).ToList();
            int linkCount = ownRecords.Count > 0 ? ownRecords.Count : peerRecords.Count;

            double nodeX;
            double nodeY;

            if (savedPositions.TryGetValue(address, out var savedCoord))
            {
                nodeX = savedCoord.X;
                nodeY = savedCoord.Y;
            }
            else
            {
                double angle = i * angleStep;
                double itemRadius = (distinctAddresses.Count > 10 && i % 2 == 1) ? radius * 0.72 : radius;
                nodeX = Math.Max(30, Math.Min(2350, centerX + itemRadius * Math.Cos(angle)));
                nodeY = Math.Max(30, Math.Min(1750, centerY + itemRadius * Math.Sin(angle)));
            }

            string label = $"Device {address}";
            string friendlyName = $"Device {address}";
            string devType = "Insteon Device";
            bool isBat = false;
            if (deviceMap.TryGetValue(address, out var devSummary) && !string.IsNullOrWhiteSpace(devSummary.Description))
            {
                friendlyName = devSummary.Description;
                label = $"{friendlyName} ({address})";
                isBat = devSummary.IsBattery;
                devType = devSummary.IsBattery ? "Battery Sensor (Sleeping)" : devSummary.Description;
            }
            else
            {
                if (ownRecords.Count > 0)
                {
                    devType = (ownRecords.Any(r => r.IsController) && ownRecords.Any(r => r.IsResponder))
                        ? "2-Way Peer (Cross-Linked)"
                        : (ownRecords.Any(r => r.IsController) ? "Controller / Keypad (Transmitter)" : "Responder / Dimmer (Load)");
                }
                else
                {
                    devType = (peerRecords.Any(r => r.IsController) && peerRecords.Any(r => r.IsResponder))
                        ? "2-Way Peer (Cross-Linked)"
                        : (peerRecords.Any(r => r.IsResponder) ? "Controller / Keypad (Transmitter)" : "Responder / Dimmer (Load)");
                }
            }

            var peerNode = new DeviceNode
            {
                Address = address,
                Label = label,
                FriendlyName = friendlyName,
                X = nodeX,
                Y = nodeY,
                IsModem = false,
                IsBattery = isBat,
                DeviceType = devType,
                LinkCount = linkCount,
                IsFilterChecked = previousChecked == null || previousChecked.Contains(address),
                IsVisible = true,
            };
            peerNode.PropertyChanged += (s, e) =>
            {
                if (e.PropertyName == nameof(DeviceNode.IsFilterChecked))
                {
                    UpdateMapVisibility();
                }
            };

            DeviceNodes.Add(peerNode);
            peerNodeMap[address] = peerNode;
        }

        // 3. Build connecting Topology Links (PLM <-> Device and Peer <-> Peer)
        var linkPairs = records
            .Where(r => r.InUse && !string.IsNullOrWhiteSpace(r.TargetAddress))
            .GroupBy(r => {
                string src = string.IsNullOrWhiteSpace(r.SourceAddress) ? plmAddress : r.SourceAddress;
                string tgt = r.TargetAddress;
                return string.Compare(src, tgt, StringComparison.OrdinalIgnoreCase) < 0
                    ? $"{src}|{tgt}|{r.Group}"
                    : $"{tgt}|{src}|{r.Group}";
            });

        foreach (var lg in linkPairs)
        {
            var firstRec = lg.First();
            string endpointA = string.IsNullOrWhiteSpace(firstRec.SourceAddress) ? plmAddress : firstRec.SourceAddress;
            string endpointB = firstRec.TargetAddress;

            DeviceNode? nodeA = (endpointA.Equals("PLM Modem", StringComparison.OrdinalIgnoreCase) || endpointA.Equals("1A.2B.3C", StringComparison.OrdinalIgnoreCase) || endpointA.Equals(plmAddress, StringComparison.OrdinalIgnoreCase)) 
                ? plmNode 
                : (peerNodeMap.TryGetValue(endpointA, out var na) ? na : null);
            DeviceNode? nodeB = (endpointB.Equals("PLM Modem", StringComparison.OrdinalIgnoreCase) || endpointB.Equals("1A.2B.3C", StringComparison.OrdinalIgnoreCase) || endpointB.Equals(plmAddress, StringComparison.OrdinalIgnoreCase)) 
                ? plmNode 
                : (peerNodeMap.TryGetValue(endpointB, out var nb) ? nb : null);

            if (nodeA == null || nodeB == null || nodeA == nodeB)
            {
                continue;
            }

            bool hasController = lg.Any(r => r.IsController);
            bool hasResponder = lg.Any(r => r.IsResponder);

            LinkKind kind;
            if (hasController && hasResponder)
            {
                kind = LinkKind.CrossLinked;
            }
            else if (hasController)
            {
                kind = LinkKind.Controller;
            }
            else
            {
                kind = LinkKind.Responder;
            }

            var link = new TopologyLink(nodeA, nodeB, firstRec.Group, kind);
            TopologyLinks.Add(link);
        }

        ApplyMapNodeSearchFilter();
        UpdateMapVisibility();
    }

    // --- Staging & Verification Workflow (Pre-Commit) ---

    public void StageSampleChange()
    {
        int nextId = StagedChanges.Count + 1;
        var samples = new[]
        {
            new StagedChangeModel
            {
                Id = $"CHG-{nextId:D3}",
                TargetAddress = "2A.44.11",
                TargetLabel = "Living Room Dimmer",
                Operation = "MODIFY_RAMP_RATE",
                CurrentState = "Ramp Rate: 0.5s, On-Level: 100%",
                ProposedState = "Ramp Rate: 2.0s, On-Level: 80%",
                Description = "Adjust ambient ramp rate and soft-start level for evening scene.",
                Risk = ChangeRisk.Low
            },
            new StagedChangeModel
            {
                Id = $"CHG-{nextId:D3}",
                TargetAddress = "4F.12.A8",
                TargetLabel = "KeypadLinc Button B",
                Operation = "ADD_SCENE_LINK",
                CurrentState = "No ALDB link on Scene 3",
                ProposedState = "Controller link to PLM (Group 3, D1=255, D2=28)",
                Description = "Link Keypad Button B to trigger Master Hallway lighting scene.",
                Risk = ChangeRisk.Medium
            },
            new StagedChangeModel
            {
                Id = $"CHG-{nextId:D3}",
                TargetAddress = "1A.2B.3C -> 5D.88.99",
                TargetLabel = "Defective Switch Replacement",
                Operation = "REPLACE_DEVICE",
                CurrentState = "Old Device 1A.2B.3C (6 peer links in PLM & network)",
                ProposedState = "New Device 5D.88.99 (Clone all 6 links & purge old address)",
                Description = "Automatic network-wide replacement: rewrite ALDB records in PLM and peers.",
                Risk = ChangeRisk.High
            }
        };

        var sample = samples[(nextId - 1) % samples.Length];
        StagedChanges.Add(sample);
        AddLog(LogLevel.Info, "Staging", $"Staged action '{sample.Operation}' on {sample.TargetAddress} for review.");
        StatusMessage = $"Added staged change '{sample.Id}'. Review in 'Pending Changes' tab before committing.";
    }

    public void SetSelectedNodeAsController(DeviceNode node)
    {
        if (node == null) return;
        SelectGraphNode(node);
        NewLinkControllerAddress = node.Address;
        SelectedTabIndex = 6;
        StatusMessage = $"Selected {node.FriendlyName} ({node.Address}) as Controller in Link Creator.";
        AddLog(LogLevel.Info, "UI", $"Selected {node.FriendlyName} ({node.Address}) as Controller in Link Creator.");
    }

    public void SetSelectedNodeAsResponder(DeviceNode node)
    {
        if (node == null) return;
        SelectGraphNode(node);
        NewLinkResponderAddress = node.Address;
        SelectedTabIndex = 6;
        StatusMessage = $"Selected {node.FriendlyName} ({node.Address}) as Responder in Link Creator.";
        AddLog(LogLevel.Info, "UI", $"Selected {node.FriendlyName} ({node.Address}) as Responder in Link Creator.");
    }

    public void SetSelectedNodeAsReplaceOld(DeviceNode node)
    {
        if (node == null) return;
        SelectGraphNode(node);
        ReplaceOldDeviceAddress = node.Address;
        SelectedTabIndex = 6;
        StatusMessage = $"Selected {node.FriendlyName} ({node.Address}) as device to replace.";
        AddLog(LogLevel.Info, "UI", $"Selected {node.FriendlyName} ({node.Address}) as device to replace.");
    }

    public void InspectNodeInAldb(string address)
    {
        if (string.IsNullOrWhiteSpace(address)) return;
        foreach (var d in SelectableDevices)
        {
            d.IsSelected = d.Address.Equals(address, StringComparison.OrdinalIgnoreCase);
        }
        RebuildOrganizedAldbView();
        SelectedTabIndex = 1;
        StatusMessage = $"Inspecting ALDB records for {address}.";
        AddLog(LogLevel.Info, "UI", $"Switched to ALDB Inspector for device {address}.");
    }

    public void StageReciprocalLink()
    {
        if (string.IsNullOrWhiteSpace(NewLinkControllerAddress) || string.IsNullOrWhiteSpace(NewLinkResponderAddress))
        {
            StatusMessage = "Both controller and responder addresses are required to stage link.";
            return;
        }

        string ctrlAddr = NewLinkControllerAddress.Trim().ToUpperInvariant();
        string respAddr = NewLinkResponderAddress.Trim().ToUpperInvariant();
        int grp = NewLinkGroup;
        int level = NewLinkOnLevel;
        int ramp = NewLinkRampRate;

        string ctrlName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(ctrlAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? ctrlAddr;
        string respName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(respAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? respAddr;

        var staged = new StagedChangeModel
        {
            Id = $"LNK-{DateTime.Now:HHmmss}",
            TargetAddress = $"{ctrlAddr} ⇄ {respAddr}",
            TargetLabel = $"{ctrlName} ⇄ {respName}",
            Operation = "CREATE_RECIPROCAL_LINK",
            CurrentState = "Unlinked (or single directional)",
            ProposedState = $"Linked (Group {grp}, OnLevel {level}, Ramp {ramp})",
            Description = $"Create verified bidirectional reciprocal link between Controller {ctrlAddr} ({ctrlName}) and Responder {respAddr} ({respName}) on Group {grp}.",
            Risk = ChangeRisk.Low,
            ExecutionHandler = async () =>
            {
                var req = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = ctrlAddr,
                    ResponderAddress = respAddr,
                    Group = grp,
                    Data1 = level,
                    Data2 = ramp,
                    Data3 = 1
                };
                return await _apiClient.CreateReciprocalLinkAsync(req);
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged reciprocal link between {ctrlName} and {respName}. Review in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Staged reciprocal link creation for {ctrlAddr} <-> {respAddr} (Group {grp}).");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public void StageDeleteLink()
    {
        if (string.IsNullOrWhiteSpace(DeleteLinkControllerAddress) || string.IsNullOrWhiteSpace(DeleteLinkResponderAddress))
        {
            StatusMessage = "Both controller and responder addresses are required to stage link deletion.";
            return;
        }

        string ctrlAddr = DeleteLinkControllerAddress.Trim().ToUpperInvariant();
        string respAddr = DeleteLinkResponderAddress.Trim().ToUpperInvariant();
        int grp = DeleteLinkGroup;

        string ctrlName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(ctrlAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? ctrlAddr;
        string respName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(respAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? respAddr;

        var staged = new StagedChangeModel
        {
            Id = $"DEL-{DateTime.Now:HHmmss}",
            TargetAddress = $"{ctrlAddr} ⇄ {respAddr}",
            TargetLabel = $"{ctrlName} ⇄ {respName}",
            Operation = "DELETE_RECIPROCAL_LINK",
            CurrentState = $"Linked on Group {grp}",
            ProposedState = "Mark in_use = False (Unlinked on both devices)",
            Description = $"Safely remove reciprocal link between Controller {ctrlAddr} ({ctrlName}) and Responder {respAddr} ({respName}) on Group {grp}.",
            Risk = ChangeRisk.Medium,
            ExecutionHandler = async () =>
            {
                return await _apiClient.DeleteReciprocalLinkAsync(ctrlAddr, respAddr, grp);
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged link deletion between {ctrlName} and {respName}. Review in Pending Changes.";
        AddLog(LogLevel.Warning, "Staging", $"Staged link deletion for {ctrlAddr} <-> {respAddr} (Group {grp}).");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public void StageDeviceReplace()
    {
        if (string.IsNullOrWhiteSpace(ReplaceOldDeviceAddress) || string.IsNullOrWhiteSpace(ReplaceNewDeviceAddress))
        {
            StatusMessage = "Both old and new device addresses are required to stage replacement.";
            return;
        }

        string oldAddr = ReplaceOldDeviceAddress.Trim().ToUpperInvariant();
        string newAddr = ReplaceNewDeviceAddress.Trim().ToUpperInvariant();
        bool copyLinks = ReplaceCopyLinks;

        string oldName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(oldAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? oldAddr;
        string newName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(newAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? newAddr;

        var staged = new StagedChangeModel
        {
            Id = $"SWAP-{DateTime.Now:HHmmss}",
            TargetAddress = $"{oldAddr} ➔ {newAddr}",
            TargetLabel = $"{oldName} ➔ {newName}",
            Operation = "REPLACE_DEVICE",
            CurrentState = $"Active device {oldAddr} with scene links",
            ProposedState = $"Retarget all peer links to {newAddr} (CloneLinks: {copyLinks})",
            Description = $"Full 'Clone & Swap' replacement: replaces {oldAddr} ({oldName}) with {newAddr} ({newName}) across PLM and all peer switches.",
            Risk = ChangeRisk.High,
            ExecutionHandler = async () =>
            {
                var req = new DeviceReplaceRequestModel
                {
                    OldAddress = oldAddr,
                    NewAddress = newAddr,
                    CopyLinks = copyLinks,
                    SanitizeOld = false
                };
                return await _apiClient.ReplaceDeviceAsync(req);
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged replacement of {oldName} with {newName}. Review in Pending Changes.";
        AddLog(LogLevel.Warning, "Staging", $"Staged device replacement: {oldAddr} -> {newAddr}.");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public async Task CommitStagedChangesAsync()
    {
        var itemsToCommit = StagedChanges.Where(c => c.IsSelected).ToList();
        if (itemsToCommit.Count == 0)
        {
            StatusMessage = "No staged changes selected to commit.";
            return;
        }

        if (ForceRefreshHardware && !IsSessionActive)
        {
            StatusMessage = "Hardware writes require an active management session. Please click 'Start Management Session' first.";
            AddLog(LogLevel.Warning, "Commit", "Commit blocked: Active hardware session required when Force Refresh Hardware is enabled.");
            return;
        }

        IsLoading = true;
        StatusMessage = $"Committing {itemsToCommit.Count} verified changes...";
        AddLog(LogLevel.Info, "Commit", $"Starting batch commit of {itemsToCommit.Count} verified network changes...");

        int successCount = 0;
        int failureCount = 0;

        try
        {
            foreach (var item in itemsToCommit)
            {
                if (item.ExecutionHandler != null)
                {
                    try
                    {
                        var (success, msg) = await item.ExecutionHandler();
                        if (success)
                        {
                            successCount++;
                            AddLog(LogLevel.Success, "Commit", $"[Verified & Committed] {item.Operation} on {item.TargetAddress}: {msg}");
                            StagedChanges.Remove(item);
                        }
                        else
                        {
                            failureCount++;
                            AddLog(LogLevel.Error, "Commit", $"[Failed] {item.Operation} on {item.TargetAddress}: {msg}");
                        }
                    }
                    catch (Exception ex)
                    {
                        failureCount++;
                        AddLog(LogLevel.Error, "Commit", $"[Error] {item.Operation} on {item.TargetAddress}: {ex.Message}");
                    }
                }
                else
                {
                    // Preview sample action
                    await Task.Delay(400);
                    successCount++;
                    AddLog(LogLevel.Success, "Commit", $"[Preview Verified] {item.Operation} on {item.TargetAddress} verified successfully.");
                    StagedChanges.Remove(item);
                }
            }

            if (successCount > 0)
            {
                await QueryAldbAsync(silent: true);
                StatusMessage = $"Committed and verified {successCount} changes successfully (Failures: {failureCount}).";
                AddLog(LogLevel.Success, "Commit", $"Topology map, ALDB tables, and scenes synchronized following commit of {successCount} changes.");
                if (failureCount == 0)
                {
                    MessageBox.Show(
                        $"Successfully committed and verified {successCount} changes across the network.",
                        "Commit Succeeded",
                        MessageBoxButton.OK,
                        MessageBoxImage.Information);
                }
                else
                {
                    MessageBox.Show(
                        $"Committed {successCount} changes, but {failureCount} changes failed to apply.\n\nPlease check the Event & Bridge Log tab for details.",
                        "Commit Partial Success",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning);
                }
            }
            else
            {
                StatusMessage = $"Commit completed with {failureCount} failures.";
                MessageBox.Show(
                    $"Failed to commit {failureCount} staged changes.\n\nPlease check the Event & Bridge Log tab for details.",
                    "Commit Failed",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Commit error: {ex.Message}";
            AddLog(LogLevel.Error, "Commit", $"Commit error: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
            CommitStagedChangesCommand?.NotifyCanExecuteChanged();
        }
    }

    public void DiscardStagedChanges()
    {
        int count = StagedChanges.Count;
        StagedChanges.Clear();
        AddLog(LogLevel.Info, "Staging", $"Discarded {count} pending changes. Hardware remains unmodified.");
        StatusMessage = $"Discarded {count} staged changes.";
    }

    private async Task ForceRestoreOpenhabAsync()
    {
        IsLoading = true;
        StatusMessage = "Emergency Override: Forcing openHAB Insteon Thing enabled...";
        AddLog(LogLevel.Warning, "Override", "Executing emergency override: Force re-enabling openHAB Insteon Thing...");
        try
        {
            var success = await _apiClient.RestoreOpenhabAsync();
            if (success)
            {
                IsSessionActive = false;
                _heartbeatTimer.Stop();
                StatusMessage = "openHAB Insteon Thing forcefully re-enabled.";
                AddLog(LogLevel.Success, "Override", "openHAB Insteon Thing restored to ONLINE state.");
                await CheckStatusAsync();
            }
            else
            {
                StatusMessage = "Failed to force restore openHAB.";
                AddLog(LogLevel.Error, "Override", "openHAB REST request failed.");
            }
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task LoadScenesAsync(bool silent = false)
    {
        try
        {
            var scenesList = await _apiClient.GetScenesAsync();
            Scenes.Clear();
            foreach (var sc in scenesList)
            {
                Scenes.Add(sc);
            }
            ApplySceneFilter();

            if (SelectedScene == null && FilteredScenes.Count > 0)
            {
                SelectedScene = FilteredScenes[0];
            }
            if (SelectedQuickScene == null && Scenes.Count > 0)
            {
                SelectedQuickScene = Scenes[0];
            }
            if (!silent)
            {
                AddLog(LogLevel.Info, "Scenes", $"Loaded {Scenes.Count} Insteon scenes from network snapshot / backend.");
            }
        }
        catch (Exception ex)
        {
            AddLog(LogLevel.Warning, "Scenes", $"Could not load scenes: {ex.Message}");
        }
    }

    private void ApplySceneFilter()
    {
        FilteredScenes.Clear();
        var q = SceneFilterText?.Trim();
        foreach (var sc in Scenes)
        {
            if (string.IsNullOrWhiteSpace(q) ||
                sc.Group.ToString().Contains(q) ||
                sc.Name.Contains(q, StringComparison.OrdinalIgnoreCase))
            {
                FilteredScenes.Add(sc);
            }
        }
    }

    public async Task TriggerSelectedSceneAsync(string action)
    {
        if (SelectedScene == null) return;
        IsLoading = true;
        StatusMessage = $"Triggering Scene {SelectedScene.Group} ({action.ToUpper()})...";
        AddLog(LogLevel.Info, "Scenes", $"Transmitting All-Link broadcast for Scene {SelectedScene.Group} ({action})...");
        try
        {
            var (success, msg) = await _apiClient.TriggerSceneAsync(SelectedScene.Group, action);
            if (success)
            {
                StatusMessage = $"Scene {SelectedScene.Group} triggered: {action.ToUpper()}";
                AddLog(LogLevel.Success, "Scenes", msg);
            }
            else
            {
                StatusMessage = $"Scene trigger failed: {msg}";
                AddLog(LogLevel.Error, "Scenes", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Scene trigger failed: {ex.Message}";
            AddLog(LogLevel.Error, "Scenes", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task UpdateMemberPresetAsync(SceneMemberModel member)
    {
        if (SelectedScene == null || member == null) return;
        IsLoading = true;
        StatusMessage = $"Updating preset for {member.Label} in Scene {SelectedScene.Group}...";
        AddLog(LogLevel.Info, "Scenes", $"Surgically updating preset for {member.Address} in Scene {SelectedScene.Group}: Level={member.OnLevel}, Ramp={member.RampRate}...");
        try
        {
            var updated = await _apiClient.UpdateSceneMemberPresetAsync(
                group: SelectedScene.Group,
                address: member.Address,
                onLevel: member.OnLevel,
                rampRate: member.RampRate
            );
            if (updated != null)
            {
                member.IsDirty = false;

                // Sync modified preset values to in-memory AldbRecords
                var matchingRecs = AldbRecords.Where(r =>
                    r.SourceAddress != null &&
                    r.SourceAddress.Equals(member.Address, StringComparison.OrdinalIgnoreCase) &&
                    r.Group == SelectedScene.Group &&
                    r.IsResponder
                ).ToList();

                foreach (var rec in matchingRecs)
                {
                    rec.Data1 = updated.OnLevel;
                    rec.Data2 = updated.RampRate;
                }

                if (!string.IsNullOrEmpty(member.MemAddr))
                {
                    var slotRec = AldbRecords.FirstOrDefault(r =>
                        r.SourceAddress != null &&
                        r.SourceAddress.Equals(member.Address, StringComparison.OrdinalIgnoreCase) &&
                        r.MemAddr.Equals(member.MemAddr, StringComparison.OrdinalIgnoreCase)
                    );
                    if (slotRec != null)
                    {
                        slotRec.Data1 = updated.OnLevel;
                        slotRec.Data2 = updated.RampRate;
                    }
                }

                ApplyFilter();
                RebuildOrganizedAldbView();

                StatusMessage = $"Preset updated successfully for {member.Label}.";
                AddLog(LogLevel.Success, "Scenes", $"Verified hardware write on {member.Address}: OnLevel={updated.OnLevel}, RampRate={updated.RampRate}.");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Preset update failed: {ex.Message}";
            AddLog(LogLevel.Error, "Scenes", $"Preset update failed: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void AnalyzeGhostLinks(List<AldbRecordModel> records)
    {
        GhostTargets.Clear();
        var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var dev in _currentDevices)
        {
            if (!string.IsNullOrWhiteSpace(dev.Address) && (dev.AldbRecordCount > 0 || dev.IsBattery))
                known.Add(dev.Address);
        }

        // Count references to unknown target addresses
        var ghostCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var rec in records)
        {
            if (rec.InUse && !string.IsNullOrWhiteSpace(rec.TargetAddress) && rec.TargetAddress != "00.00.00" && !known.Contains(rec.TargetAddress))
            {
                ghostCounts[rec.TargetAddress] = ghostCounts.GetValueOrDefault(rec.TargetAddress, 0) + 1;
            }
        }

        foreach (var kvp in ghostCounts.OrderByDescending(k => k.Value))
        {
            GhostTargets.Add($"{kvp.Key} ({kvp.Value} dead links)");
        }

        if (GhostTargets.Count > 0 && string.IsNullOrEmpty(SelectedGhostTarget))
        {
            SelectedGhostTarget = GhostTargets[0];
        }
    }

    public async Task PurgeSelectedGhostTargetAsync()
    {
        if (string.IsNullOrWhiteSpace(SelectedGhostTarget)) return;

        // Extract raw address (e.g. "2C.E7.EF" from "2C.E7.EF (63 dead links)")
        var targetAddr = SelectedGhostTarget.Split(' ')[0].Trim();

        IsLoading = true;
        StatusMessage = $"Purging all ghost references to {targetAddr} across the network...";
        AddLog(LogLevel.Warning, "NetworkTools", $"Initiating network-wide purge of orphan references to {targetAddr}...");
        try
        {
            var (success, msg) = await _apiClient.PurgeGhostLinksAsync(targetAddr);
            if (success)
            {
                StatusMessage = $"Ghost links purged for {targetAddr}.";
                AddLog(LogLevel.Success, "NetworkTools", msg);
                await QueryAldbAsync(); // Refresh database & topology
            }
            else
            {
                StatusMessage = $"Ghost purge failed: {msg}";
                AddLog(LogLevel.Error, "NetworkTools", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Ghost purge failed: {ex.Message}";
            AddLog(LogLevel.Error, "NetworkTools", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task ExecuteDeviceReplaceAsync()
    {
        if (string.IsNullOrWhiteSpace(ReplaceOldDeviceAddress) || string.IsNullOrWhiteSpace(ReplaceNewDeviceAddress))
        {
            StatusMessage = "Both old and new device addresses are required for replacement.";
            return;
        }

        IsLoading = true;
        StatusMessage = $"Executing 'Clone & Swap' replacement: {ReplaceOldDeviceAddress} -> {ReplaceNewDeviceAddress}...";
        AddLog(LogLevel.Warning, "NetworkTools", $"Executing 'Clone & Swap' replacement of {ReplaceOldDeviceAddress} with {ReplaceNewDeviceAddress}...");
        try
        {
            var req = new DeviceReplaceRequestModel
            {
                OldAddress = ReplaceOldDeviceAddress.Trim(),
                NewAddress = ReplaceNewDeviceAddress.Trim(),
                CopyLinks = ReplaceCopyLinks,
                SanitizeOld = false
            };
            var (success, msg) = await _apiClient.ReplaceDeviceAsync(req);
            if (success)
            {
                StatusMessage = $"Device replacement complete: {ReplaceOldDeviceAddress} replaced by {ReplaceNewDeviceAddress}.";
                AddLog(LogLevel.Success, "DeviceReplace", msg);
                await QueryAldbAsync(silent: true);
                AddLog(LogLevel.Success, "Network", $"Topology map and ALDB tables updated following device replacement.");
            }
            else
            {
                StatusMessage = $"Device replacement failed: {msg}";
                AddLog(LogLevel.Error, "NetworkTools", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Device replacement failed: {ex.Message}";
            AddLog(LogLevel.Error, "NetworkTools", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task CreateReciprocalLinkAsync()
    {
        if (string.IsNullOrWhiteSpace(NewLinkControllerAddress) || string.IsNullOrWhiteSpace(NewLinkResponderAddress))
        {
            StatusMessage = "Both controller and responder addresses are required.";
            return;
        }

        IsLoading = true;
        StatusMessage = $"Creating verified reciprocal link: {NewLinkControllerAddress} <-> {NewLinkResponderAddress} for Group {NewLinkGroup}...";
        AddLog(LogLevel.Info, "LinkCreator", $"Initiating atomic 2-phase link between {NewLinkControllerAddress} and {NewLinkResponderAddress} (Group {NewLinkGroup})...");
        try
        {
            var req = new ReciprocalLinkRequestModel
            {
                ControllerAddress = NewLinkControllerAddress.Trim(),
                ResponderAddress = NewLinkResponderAddress.Trim(),
                Group = NewLinkGroup,
                Data1 = NewLinkOnLevel,
                Data2 = NewLinkRampRate,
                Data3 = 1
            };
            var (success, msg) = await _apiClient.CreateReciprocalLinkAsync(req);
            if (success)
            {
                StatusMessage = $"Reciprocal link created successfully: {NewLinkControllerAddress} <-> {NewLinkResponderAddress} (Group {NewLinkGroup}).";
                AddLog(LogLevel.Success, "LinkCreator", msg);
                await QueryAldbAsync(silent: true);
                AddLog(LogLevel.Success, "Network", $"Topology map, ALDB tables, and scenes updated for link {NewLinkControllerAddress} <-> {NewLinkResponderAddress}.");
            }
            else
            {
                StatusMessage = $"Link creation failed: {msg}";
                AddLog(LogLevel.Error, "LinkCreator", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Link creation failed: {ex.Message}";
            AddLog(LogLevel.Error, "LinkCreator", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task DeleteReciprocalLinkAsync()
    {
        if (string.IsNullOrWhiteSpace(DeleteLinkControllerAddress) || string.IsNullOrWhiteSpace(DeleteLinkResponderAddress))
        {
            StatusMessage = "Both controller and responder addresses are required to delete link.";
            return;
        }

        var result = MessageBox.Show(
            $"Are you sure you want to delete the reciprocal link between Controller {DeleteLinkControllerAddress} and Responder {DeleteLinkResponderAddress} (Group {DeleteLinkGroup})?\n\nThis will remove the link from both devices.",
            "Confirm Delete Reciprocal Link",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning
        );

        if (result != MessageBoxResult.Yes) return;

        IsLoading = true;
        StatusMessage = $"Deleting reciprocal link: {DeleteLinkControllerAddress} <-> {DeleteLinkResponderAddress} (Group {DeleteLinkGroup})...";
        AddLog(LogLevel.Warning, "LinkDeleter", $"Initiating atomic deletion of link between {DeleteLinkControllerAddress} and {DeleteLinkResponderAddress} (Group {DeleteLinkGroup})...");

        try
        {
            var (success, msg) = await _apiClient.DeleteReciprocalLinkAsync(
                controllerAddr: DeleteLinkControllerAddress.Trim(),
                responderAddr: DeleteLinkResponderAddress.Trim(),
                group: DeleteLinkGroup
            );

            if (success)
            {
                StatusMessage = $"Reciprocal link deleted successfully: {DeleteLinkControllerAddress} <-> {DeleteLinkResponderAddress}.";
                AddLog(LogLevel.Success, "LinkDeleter", msg);
                await QueryAldbAsync(silent: true);
                AddLog(LogLevel.Success, "Network", $"Topology map and ALDB tables updated following deletion of {DeleteLinkControllerAddress} <-> {DeleteLinkResponderAddress}.");
            }
            else
            {
                StatusMessage = $"Link deletion failed: {msg}";
                AddLog(LogLevel.Error, "NetworkTools", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Link deletion error: {ex.Message}";
            AddLog(LogLevel.Error, "NetworkTools", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task DeleteLinkFromEntryAsync(OrganizedAldbEntry? entry)
    {
        if (entry == null) return;

        if (entry.IsDuplicate || entry.IsGroupZero)
        {
            string reason = entry.IsDuplicate
                ? $"Record {entry.MemAddr} is a redundant duplicate of an active link with {entry.TargetDisplay} on Group {entry.Group}."
                : $"Record {entry.MemAddr} is an invalid/orphaned Group 0 record.";

            var slotConfirm = MessageBox.Show(
                $"{reason}\n\nDo you want to clean up (delete) this individual record slot {entry.MemAddr} on {entry.OwnerFriendlyName} ({entry.OwnerAddress})?\n\n(This safely clears this specific memory slot without affecting other active links).",
                "Clean Up ALDB Record Slot",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question
            );
            if (slotConfirm != MessageBoxResult.Yes) return;

            IsLoading = true;
            try
            {
                var (s, m) = await _apiClient.DeleteDeviceAldbRecordAsync(entry.OwnerAddress, entry.MemAddr);
                if (s)
                {
                    StatusMessage = $"Cleaned up ALDB slot {entry.MemAddr} on {entry.OwnerAddress}.";
                    AddLog(LogLevel.Success, "ALDB", m);
                    await QueryAldbAsync(silent: true);
                }
                else
                {
                    StatusMessage = $"Delete failed: {m}";
                    AddLog(LogLevel.Error, "ALDB", m);
                }
            }
            finally
            {
                IsLoading = false;
            }
            return;
        }

        string ctrlAddr;
        string respAddr;

        if (entry.IsController)
        {
            ctrlAddr = entry.OwnerAddress;
            respAddr = entry.TargetAddress;
        }
        else if (entry.IsResponder)
        {
            ctrlAddr = entry.TargetAddress;
            respAddr = entry.OwnerAddress;
        }
        else
        {
            var slotConfirm = MessageBox.Show(
                $"Delete ALDB record slot {entry.MemAddr} on device {entry.OwnerAddress}?",
                "Confirm Delete ALDB Record",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning
            );
            if (slotConfirm != MessageBoxResult.Yes) return;

            IsLoading = true;
            try
            {
                var (s, m) = await _apiClient.DeleteDeviceAldbRecordAsync(entry.OwnerAddress, entry.MemAddr);
                if (s)
                {
                    StatusMessage = $"Slot {entry.MemAddr} deleted successfully.";
                    AddLog(LogLevel.Success, "ALDB", m);
                    await QueryAldbAsync();
                }
                else
                {
                    StatusMessage = $"Delete failed: {m}";
                    AddLog(LogLevel.Error, "ALDB", m);
                }
            }
            finally
            {
                IsLoading = false;
            }
            return;
        }

        var confirm = MessageBox.Show(
            $"Delete reciprocal link between Controller {ctrlAddr} and Responder {respAddr} on Group {entry.Group}?\n\nThis will remove the link from both devices.",
            "Confirm Delete Link",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning
        );

        if (confirm != MessageBoxResult.Yes) return;

        IsLoading = true;
        StatusMessage = $"Deleting reciprocal link between {ctrlAddr} and {respAddr} (Group {entry.Group})...";
        AddLog(LogLevel.Warning, "ALDB", $"Deleting reciprocal link: {ctrlAddr} <-> {respAddr} (Group {entry.Group})...");

        try
        {
            var (success, msg) = await _apiClient.DeleteReciprocalLinkAsync(ctrlAddr, respAddr, entry.Group);
            if (success)
            {
                StatusMessage = $"Link deleted successfully: {ctrlAddr} <-> {respAddr}.";
                AddLog(LogLevel.Success, "ALDB", msg);
                await QueryAldbAsync(silent: true);
                AddLog(LogLevel.Success, "Network", "ALDB records and topology refreshed.");
            }
            else
            {
                StatusMessage = $"Failed to delete link: {msg}";
                AddLog(LogLevel.Error, "ALDB", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error deleting link: {ex.Message}";
            AddLog(LogLevel.Error, "ALDB", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void RebuildActiveLinkPairs(List<AldbRecordModel> records)
    {
        ActiveLinkPairs.Clear();
        var friendlyMap = SelectableDevices.ToDictionary(d => d.Address, d => d.FriendlyName, StringComparer.OrdinalIgnoreCase);

        var ctrlRecords = records.Where(r => 
            r.InUse && !r.HighWaterMark && r.IsController && r.Group >= 1 && !string.IsNullOrWhiteSpace(r.SourceAddress) && !string.IsNullOrWhiteSpace(r.TargetAddress)
        ).OrderBy(r => r.SourceAddress).ThenBy(r => r.Group).ToList();

        foreach (var c in ctrlRecords)
        {
            string srcName = friendlyMap.TryGetValue(c.SourceAddress!, out var sn) ? sn : c.SourceAddress!;
            string tgtName = friendlyMap.TryGetValue(c.TargetAddress, out var tn) ? tn : c.TargetAddress;

            ActiveLinkPairs.Add(new ActiveLinkPairModel
            {
                ControllerAddress = c.SourceAddress!,
                ControllerFriendlyName = srcName,
                ResponderAddress = c.TargetAddress,
                ResponderFriendlyName = tgtName,
                Group = c.Group,
                MemAddrController = c.MemAddr,
            });
        }
    }

    public void SelectGraphNode(DeviceNode node)
    {
        SelectedGraphNode = node;
    }

    public async Task ControlDeviceFromStringAsync(string? param)
    {
        if (string.IsNullOrWhiteSpace(param)) return;
        var parts = param.Split(':');
        var addr = parts[0].Trim();
        var action = parts.Length > 1 ? parts[1].Trim().ToLower() : "on";
        int level = 255;
        if (action == "dim" || action == "set_level")
        {
            if (parts.Length > 2 && int.TryParse(parts[2], out int parsedLvl))
            {
                level = parsedLvl;
            }
            else
            {
                level = (int)Math.Round((SelectedNodeBrightness / 100.0) * 255);
            }
        }
        else if (action == "off" || action == "fast_off")
        {
            level = 0;
        }

        await ExecuteDeviceControlAsync(addr, action, level);
    }

    public async Task ControlSelectedNodeAsync(string? action)
    {
        if (SelectedGraphNode == null) return;
        var act = string.IsNullOrWhiteSpace(action) ? "on" : action.Trim().ToLower();
        int level = 255;
        if (act == "dim" || act == "set_level")
        {
            level = (int)Math.Round((SelectedNodeBrightness / 100.0) * 255);
        }
        else if (act == "off" || act == "fast_off")
        {
            level = 0;
        }

        await ExecuteDeviceControlAsync(SelectedGraphNode.Address, act, level);
    }

    public async Task ExecuteDeviceControlAsync(string address, string action, int level = 255)
    {
        if (string.IsNullOrWhiteSpace(address)) return;

        StatusMessage = $"Sending '{action}' command to {address}...";
        AddLog(LogLevel.Info, "DeviceControl", $"Dispatching '{action}' (level={level}) to {address}...");

        try
        {
            var (success, msg) = await _apiClient.ControlDeviceAsync(address, action, level);
            if (success)
            {
                StatusMessage = $"Device {address} {action} succeeded.";
                AddLog(LogLevel.Success, "DeviceControl", $"Device {address}: {msg}");
            }
            else
            {
                StatusMessage = $"Device {address} {action} failed: {msg}";
                AddLog(LogLevel.Warning, "DeviceControl", $"Device {address} failed: {msg}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error sending {action} to {address}: {ex.Message}";
            AddLog(LogLevel.Error, "DeviceControl", $"Control error: {ex.Message}");
        }
    }

    public async Task QuickTriggerSceneAsync(string? action)
    {
        if (SelectedQuickScene == null) return;
        var act = string.IsNullOrWhiteSpace(action) ? "on" : action.Trim().ToLower();

        StatusMessage = $"Triggering Scene {SelectedQuickScene.Group} ({SelectedQuickScene.Name}) with '{act}'...";
        AddLog(LogLevel.Info, "SceneTrigger", $"Broadcasting All-Link '{act}' to Scene {SelectedQuickScene.Group}...");

        try
        {
            var (success, msg) = await _apiClient.TriggerSceneAsync(SelectedQuickScene.Group, act);
            if (success)
            {
                StatusMessage = $"Scene {SelectedQuickScene.Group} triggered: {act}";
                AddLog(LogLevel.Success, "SceneTrigger", $"Scene {SelectedQuickScene.Group}: {msg}");
            }
            else
            {
                StatusMessage = $"Scene {SelectedQuickScene.Group} trigger failed: {msg}";
                AddLog(LogLevel.Warning, "SceneTrigger", $"Scene {SelectedQuickScene.Group} failed: {msg}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error triggering scene: {ex.Message}";
            AddLog(LogLevel.Error, "SceneTrigger", $"Trigger error: {ex.Message}");
        }
    }

    public async Task AuditDefaultLinksAsync(bool silent = false)
    {
        try
        {
            var audit = await _apiClient.AuditDefaultLinksAsync();
            MissingDefaultLinks.Clear();
            if (audit != null)
            {
                foreach (var item in audit.Items)
                {
                    MissingDefaultLinks.Add(item);
                }
            }

            OnPropertyChanged(nameof(MissingDefaultLinksCount));
            OnPropertyChanged(nameof(HasMissingDefaultLinks));
            OnPropertyChanged(nameof(DefaultLinkAuditStatusText));
            StageFixAllDefaultLinksCommand?.NotifyCanExecuteChanged();

            // Sync with organized device cards
            foreach (var org in OrganizedDevices)
            {
                var missing = MissingDefaultLinks.FirstOrDefault(m => m.DeviceAddress.Equals(org.Address, StringComparison.OrdinalIgnoreCase));
                org.HasMissingDefaultLink = (missing != null);
                org.DefaultLinkIssue = missing?.IssueSummary ?? string.Empty;
                org.DefaultLinkRecommendation = missing?.Recommendation ?? string.Empty;
            }

            if (!silent)
            {
                if (MissingDefaultLinks.Count > 0)
                {
                    StatusMessage = $"Default Link Audit: {MissingDefaultLinks.Count} devices missing reciprocal PLM links.";
                    AddLog(LogLevel.Warning, "PLMAudit", $"Found {MissingDefaultLinks.Count} devices missing default reciprocal PLM links.");
                }
                else
                {
                    StatusMessage = "Default Link Audit: All devices have verified default reciprocal links with PLM.";
                    AddLog(LogLevel.Success, "PLMAudit", "All network devices have verified default reciprocal links with PLM modem.");
                }
            }
        }
        catch (Exception ex)
        {
            if (!silent)
            {
                StatusMessage = $"Default link audit error: {ex.Message}";
                AddLog(LogLevel.Error, "PLMAudit", $"Audit error: {ex.Message}");
            }
        }
    }

    public void StageDefaultLinkForDevice(object? target, bool switchTab = true)
    {
        string? devAddr = null;
        string? devName = null;
        string? issue = null;

        if (target is MissingDefaultLinkItemModel item)
        {
            devAddr = item.DeviceAddress;
            devName = item.DeviceName;
            issue = item.IssueSummary;
        }
        else if (target is OrganizedDeviceAldb org)
        {
            devAddr = org.Address;
            devName = org.FriendlyName;
            issue = org.DefaultLinkIssue;
        }
        else if (target is string addrStr)
        {
            devAddr = InsteonAddressHelper.Normalize(addrStr);
            devName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(devAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? devAddr;
        }

        if (string.IsNullOrWhiteSpace(devAddr)) return;

        string plm = PlmModemAddress;
        string name = string.IsNullOrWhiteSpace(devName) ? devAddr : devName;

        // Check if already staged
        if (StagedChanges.Any(c => c.TargetAddress.Contains(devAddr) && c.Operation == "CREATE_DEFAULT_PLM_LINK"))
        {
            StatusMessage = $"Default link for {name} ({devAddr}) is already staged.";
            if (switchTab) SelectedTabIndex = 4;
            return;
        }

        var staged = new StagedChangeModel
        {
            Id = $"DEF-{DateTime.Now:HHmmss}-{StagedChanges.Count + 1}",
            TargetAddress = $"{plm} ⇄ {devAddr}",
            TargetLabel = $"PLM Modem ⇄ {name}",
            Operation = "CREATE_DEFAULT_PLM_LINK",
            CurrentState = issue ?? "Missing default Group 1 link pair with PLM",
            ProposedState = $"Reciprocal Group 1 Link Pair with PLM ({plm})",
            Description = $"Establish default reciprocal All-Link Group 1 records between PLM ({plm}) and {name} ({devAddr}) for status reporting and remote control.",
            Risk = ChangeRisk.Low,
            ExecutionHandler = async () =>
            {
                // Link 1: PLM (Controller) <-> Device (Responder) on Group 1
                var req1 = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = plm,
                    ResponderAddress = devAddr,
                    Group = 1,
                    Data1 = 255,
                    Data2 = 28,
                    Data3 = 1
                };
                var (s1, m1) = await _apiClient.CreateReciprocalLinkAsync(req1);
                if (!s1) return (false, $"PLM->Device link failed: {m1}");

                // Link 2: Device (Controller) <-> PLM (Responder) on Group 1
                var req2 = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = devAddr,
                    ResponderAddress = plm,
                    Group = 1,
                    Data1 = 255,
                    Data2 = 28,
                    Data3 = 1
                };
                var (s2, m2) = await _apiClient.CreateReciprocalLinkAsync(req2);
                if (!s2) return (false, $"Device->PLM link failed: {m2}");

                return (true, $"Reciprocal default Group 1 link pair established between PLM {plm} and {devAddr}.");
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged default PLM link pair for {name} ({devAddr}). Review in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Staged default PLM link pair: {plm} <-> {devAddr} (Group 1).");
        if (switchTab) SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public async Task FixDefaultLinkForDeviceAsync(object? target)
    {
        string? devAddr = null;
        string? devName = null;

        if (target is MissingDefaultLinkItemModel item)
        {
            devAddr = item.DeviceAddress;
            devName = item.DeviceName;
        }
        else if (target is OrganizedDeviceAldb org)
        {
            devAddr = org.Address;
            devName = org.FriendlyName;
        }
        else if (target is string addrStr)
        {
            devAddr = InsteonAddressHelper.Normalize(addrStr);
            devName = SelectableDevices.FirstOrDefault(d => d.Address.Equals(devAddr, StringComparison.OrdinalIgnoreCase))?.FriendlyName ?? devAddr;
        }

        if (string.IsNullOrWhiteSpace(devAddr)) return;

        IsLoading = true;
        string plm = PlmModemAddress;
        string name = string.IsNullOrWhiteSpace(devName) ? devAddr : devName;
        StatusMessage = $"Creating default PLM link pair for {name} ({devAddr})...";
        AddLog(LogLevel.Info, "DefaultLink", $"Establishing default reciprocal Group 1 links between PLM {plm} and {devAddr}...");

        try
        {
            // Link 1: PLM (Controller) <-> Device (Responder) on Group 1
            var req1 = new ReciprocalLinkRequestModel
            {
                ControllerAddress = plm,
                ResponderAddress = devAddr,
                Group = 1,
                Data1 = 255,
                Data2 = 28,
                Data3 = 1
            };
            var (s1, m1) = await _apiClient.CreateReciprocalLinkAsync(req1);
            if (!s1)
            {
                StatusMessage = $"Failed to create PLM->Device link: {m1}";
                AddLog(LogLevel.Error, "DefaultLink", m1);
                return;
            }

            // Link 2: Device (Controller) <-> PLM (Responder) on Group 1
            var req2 = new ReciprocalLinkRequestModel
            {
                ControllerAddress = devAddr,
                ResponderAddress = plm,
                Group = 1,
                Data1 = 255,
                Data2 = 28,
                Data3 = 1
            };
            var (s2, m2) = await _apiClient.CreateReciprocalLinkAsync(req2);
            if (!s2)
            {
                StatusMessage = $"Failed to create Device->PLM link: {m2}";
                AddLog(LogLevel.Error, "DefaultLink", m2);
                return;
            }

            StatusMessage = $"Default PLM links created successfully for {name}.";
            AddLog(LogLevel.Success, "DefaultLink", $"Verified reciprocal default links created between PLM {plm} and {devAddr}.");

            // Immediately clear missing default link state on the device card and audit collection
            var targetOrg = OrganizedDevices.FirstOrDefault(o => o.Address.Equals(devAddr, StringComparison.OrdinalIgnoreCase));
            if (targetOrg != null)
            {
                targetOrg.HasMissingDefaultLink = false;
                targetOrg.DefaultLinkIssue = string.Empty;
                targetOrg.DefaultLinkRecommendation = string.Empty;
            }
            var missingItem = MissingDefaultLinks.FirstOrDefault(m => m.DeviceAddress.Equals(devAddr, StringComparison.OrdinalIgnoreCase));
            if (missingItem != null)
            {
                MissingDefaultLinks.Remove(missingItem);
                OnPropertyChanged(nameof(MissingDefaultLinksCount));
                OnPropertyChanged(nameof(HasMissingDefaultLinks));
                OnPropertyChanged(nameof(DefaultLinkAuditStatusText));
                StageFixAllDefaultLinksCommand?.NotifyCanExecuteChanged();
            }

            await QueryAldbAsync(silent: true);
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error creating default links: {ex.Message}";
            AddLog(LogLevel.Error, "DefaultLink", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void StageFixAllDefaultLinks()
    {
        var missingList = MissingDefaultLinks.ToList();
        if (missingList.Count == 0)
        {
            StatusMessage = "No devices missing default PLM links.";
            return;
        }

        int count = 0;
        foreach (var item in missingList)
        {
            StageDefaultLinkForDevice(item, switchTab: false);
            count++;
        }

        StatusMessage = $"Staged {count} default PLM link pairs in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Batch-staged default reciprocal PLM links for {count} devices.");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public void StageAddDevice()
    {
        if (!InsteonAddressHelper.IsValid(NewDeviceAddress))
        {
            StatusMessage = "Valid Insteon address required to stage new device.";
            return;
        }

        string cleanAddr = InsteonAddressHelper.Normalize(NewDeviceAddress);
        string name = string.IsNullOrWhiteSpace(NewDeviceName) ? $"Device {cleanAddr}" : NewDeviceName.Trim();
        var catOption = SelectedDeviceCategory ?? DeviceCategoryOptions.FirstOrDefault();
        int cat = catOption?.Cat ?? 1;
        int subcat = catOption?.Subcat ?? 0;
        bool createDefault = NewDeviceCreateDefaultLinks;

        var staged = new StagedChangeModel
        {
            Id = $"DEV-{DateTime.Now:HHmmss}",
            TargetAddress = cleanAddr,
            TargetLabel = name,
            Operation = "ADD_DEVICE",
            CurrentState = "Unregistered device",
            ProposedState = $"Add to network as {catOption?.Name ?? "Insteon Device"} (CreateDefaultLinks: {createDefault})",
            Description = $"Register new device {cleanAddr} ({name}) into network and automatically create default reciprocal Group 1 links with PLM {PlmModemAddress}.",
            Risk = ChangeRisk.Low,
            ExecutionHandler = async () =>
            {
                var req = new DeviceAddRequestModel
                {
                    Address = cleanAddr,
                    Description = name,
                    Cat = cat,
                    Subcat = subcat,
                    CreateDefaultLinks = createDefault
                };
                var (success, msg, _) = await _apiClient.AddDeviceAsync(req);
                return (success, msg);
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged device registration for {name} ({cleanAddr}). Review in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Staged new device registration: {cleanAddr} ({name}).");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public async Task AddDeviceNowAsync()
    {
        if (!InsteonAddressHelper.IsValid(NewDeviceAddress))
        {
            StatusMessage = "Valid Insteon address required.";
            return;
        }

        string cleanAddr = InsteonAddressHelper.Normalize(NewDeviceAddress);
        string name = string.IsNullOrWhiteSpace(NewDeviceName) ? $"Device {cleanAddr}" : NewDeviceName.Trim();
        var catOption = SelectedDeviceCategory ?? DeviceCategoryOptions.FirstOrDefault();
        int cat = catOption?.Cat ?? 1;
        int subcat = catOption?.Subcat ?? 0;
        bool createDefault = NewDeviceCreateDefaultLinks;

        IsLoading = true;
        StatusMessage = $"Adding device {cleanAddr} ({name})...";
        AddLog(LogLevel.Info, "DeviceAdd", $"Registering new device {cleanAddr} ({name}) with category {catOption?.Name}...");

        try
        {
            var req = new DeviceAddRequestModel
            {
                Address = cleanAddr,
                Description = name,
                Cat = cat,
                Subcat = subcat,
                CreateDefaultLinks = createDefault
            };
            var (success, msg, _) = await _apiClient.AddDeviceAsync(req);
            if (success)
            {
                StatusMessage = msg;
                AddLog(LogLevel.Success, "DeviceAdd", msg);
                NewDeviceAddress = string.Empty;
                NewDeviceName = string.Empty;
                await QueryAldbAsync(silent: true);
            }
            else
            {
                StatusMessage = $"Failed to add device: {msg}";
                AddLog(LogLevel.Error, "DeviceAdd", msg);
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error adding device: {ex.Message}";
            AddLog(LogLevel.Error, "DeviceAdd", ex.Message);
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void ApplyAnalyzerFilter()
    {
        FilteredNetworkIssues.Clear();
        var filter = SelectedAnalyzerFilter?.ToLowerInvariant() ?? "all";
        foreach (var issue in NetworkIssues)
        {
            bool match = filter switch
            {
                "all" => true,
                "half_link" => issue.IssueType == "half_link",
                "loopback" => issue.IssueType == "loopback",
                "keypad" => (issue.OwnerName?.Contains("Keypad", StringComparison.OrdinalIgnoreCase) ?? false) ||
                            (issue.TargetName?.Contains("Keypad", StringComparison.OrdinalIgnoreCase) ?? false) ||
                            issue.OwnerAddress.Equals("1C.2C.40", StringComparison.OrdinalIgnoreCase) ||
                            issue.TargetAddress.Equals("1C.2C.40", StringComparison.OrdinalIgnoreCase),
                "missing_default" => issue.IssueType == "missing_default",
                "ghost" => issue.IssueType == "ghost",
                _ => true
            };
            if (match)
            {
                FilteredNetworkIssues.Add(issue);
            }
        }
    }

    public async Task RunNetworkAnalysisAsync(bool silent = false)
    {
        if (IsAnalyzingNetwork) return;
        IsAnalyzingNetwork = true;
        if (!silent)
        {
            StatusMessage = "Analyzing Insteon network topology and link reciprocity...";
            AddLog(LogLevel.Info, "Analyzer", "Beginning deep diagnostic network analysis...");
        }

        try
        {
            var report = await _apiClient.AnalyzeNetworkAsync();
            if (report != null)
            {
                NetworkHealthScore = report.HealthScore;
                AnalyzerTotalIssuesCount = report.TotalIssues;
                AnalyzerHalfLinksCount = report.HalfLinksCount;
                AnalyzerLoopbacksCount = report.LoopbacksCount;
                AnalyzerMissingPlmCount = report.MissingDefaultsCount;
                AnalyzerGhostCount = report.GhostsCount;

                NetworkIssues.Clear();
                foreach (var issue in report.Issues)
                {
                    NetworkIssues.Add(issue);
                }

                ApplyAnalyzerFilter();

                FixAllHalfLinksCommand?.NotifyCanExecuteChanged();
                StageAllAnalyzerFixesCommand?.NotifyCanExecuteChanged();

                if (!silent)
                {
                    StatusMessage = $"Analysis complete: Health Score {report.HealthScore}%. Found {report.TotalIssues} network issues ({report.HalfLinksCount} broken half-links, {report.LoopbacksCount} loopbacks).";
                    AddLog(LogLevel.Success, "Analyzer", $"Analysis complete: Health Score {report.HealthScore}%. {report.TotalIssues} issues detected across {report.TotalDevices} devices.");
                }
            }
            else
            {
                if (!silent)
                {
                    StatusMessage = "Network analysis returned no response.";
                    AddLog(LogLevel.Warning, "Analyzer", "Network analysis returned empty report.");
                }
            }
        }
        catch (Exception ex)
        {
            if (!silent)
            {
                StatusMessage = $"Analysis error: {ex.Message}";
                AddLog(LogLevel.Error, "Analyzer", $"Analysis failed: {ex.Message}");
            }
        }
        finally
        {
            IsAnalyzingNetwork = false;
        }
    }

    public async Task FixNetworkIssueAsync(NetworkIssueItemModel? issue)
    {
        if (issue == null) return;
        IsLoading = true;
        StatusMessage = $"Executing fix for {issue.Summary}...";
        AddLog(LogLevel.Info, "Analyzer", $"Executing fix for issue: {issue.Summary} ({issue.RecommendedAction}).");

        try
        {
            bool success = false;
            string message = string.Empty;

            if (issue.RecommendedAction == "create_reciprocal")
            {
                var req = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = issue.ControllerAddress,
                    ResponderAddress = issue.ResponderAddress,
                    Group = issue.Group,
                    Data1 = issue.Data1,
                    Data2 = issue.Data2,
                    Data3 = issue.Data3
                };
                var res = await _apiClient.CreateReciprocalLinkAsync(req);
                success = res.Success;
                message = res.Message;
            }
            else if (issue.RecommendedAction == "delete_record")
            {
                if (!string.IsNullOrEmpty(issue.MemAddr))
                {
                    var res = await _apiClient.DeleteDeviceAldbRecordAsync(issue.OwnerAddress, issue.MemAddr);
                    success = res.Success;
                    message = res.Message;
                }
            }
            else if (issue.RecommendedAction == "create_default_link")
            {
                string devAddr = issue.OwnerAddress;
                string plm = PlmModemAddress;
                var req1 = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = plm,
                    ResponderAddress = devAddr,
                    Group = 1,
                    Data1 = 255,
                    Data2 = 28,
                    Data3 = 1
                };
                var (s1, m1) = await _apiClient.CreateReciprocalLinkAsync(req1);
                if (!s1)
                {
                    success = false;
                    message = m1;
                }
                else
                {
                    var req2 = new ReciprocalLinkRequestModel
                    {
                        ControllerAddress = devAddr,
                        ResponderAddress = plm,
                        Group = 1,
                        Data1 = 255,
                        Data2 = 28,
                        Data3 = 1
                    };
                    var res2 = await _apiClient.CreateReciprocalLinkAsync(req2);
                    success = res2.Success;
                    message = res2.Message;
                }
            }
            else if (issue.RecommendedAction == "purge_ghost")
            {
                var res = await _apiClient.PurgeGhostLinksAsync(issue.TargetAddress);
                success = res.Success;
                message = res.Message;
            }

            if (success)
            {
                StatusMessage = $"Fixed: {message}";
                AddLog(LogLevel.Success, "Analyzer", $"Resolved issue {issue.Id}: {message}");
                await QueryAldbAsync(silent: true);
            }
            else
            {
                StatusMessage = $"Failed to fix issue: {message}";
                AddLog(LogLevel.Error, "Analyzer", $"Failed to resolve {issue.Id}: {message}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error executing fix: {ex.Message}";
            AddLog(LogLevel.Error, "Analyzer", $"Exception during issue fix: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    public async Task DeleteNetworkIssueSlotAsync(NetworkIssueModel? issue)
    {
        if (issue == null || string.IsNullOrEmpty(issue.MemAddr)) return;

        var confirm = MessageBox.Show(
            $"Are you sure you want to delete invalid ALDB record slot {issue.MemAddr} on device {issue.OwnerName} ({issue.OwnerAddress})?",
            "Confirm Slot Deletion",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question);

        if (confirm != MessageBoxResult.Yes) return;

        IsLoading = true;
        StatusMessage = $"Deleting slot {issue.MemAddr} on {issue.OwnerAddress}...";
        AddLog(LogLevel.Info, "Analyzer", $"Deleting corrupted slot {issue.MemAddr} on {issue.OwnerAddress}...");

        try
        {
            var res = await _apiClient.DeleteDeviceAldbRecordAsync(issue.OwnerAddress, issue.MemAddr);
            if (res.Success)
            {
                StatusMessage = $"Slot {issue.MemAddr} deleted successfully.";
                AddLog(LogLevel.Success, "Analyzer", $"Slot {issue.MemAddr} on {issue.OwnerAddress} cleared.");
                await QueryAldbAsync(silent: true);
            }
            else
            {
                StatusMessage = $"Failed to delete slot: {res.Message}";
                AddLog(LogLevel.Error, "Analyzer", $"Failed deleting slot {issue.MemAddr}: {res.Message}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"Error deleting slot: {ex.Message}";
            AddLog(LogLevel.Error, "Analyzer", $"Error deleting slot: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void StageNetworkIssueFix(NetworkIssueModel? issue)
    {
        if (issue == null) return;

        string proposedState;
        if (issue.RecommendedAction == "create_reciprocal")
        {
            proposedState = $"Create reciprocal link between Controller {issue.ControllerAddress} ({issue.ControllerName}) and Responder {issue.ResponderAddress} ({issue.ResponderName}) on Group {issue.Group}";
        }
        else if (issue.RecommendedAction == "create_default_link")
        {
            proposedState = $"Establish default bidirectional Group 1 links with PLM {PlmModemAddress}";
        }
        else if (issue.RecommendedAction == "purge_ghost")
        {
            proposedState = $"Purge all references to decommissioned device {issue.TargetAddress}";
        }
        else
        {
            proposedState = $"Mark slot {issue.MemAddr} on {issue.OwnerAddress} ({issue.OwnerName}) as deleted";
        }

        string targetAddress = !string.IsNullOrEmpty(issue.ControllerAddress) && !string.IsNullOrEmpty(issue.ResponderAddress)
            ? $"{issue.ControllerAddress} ⇄ {issue.ResponderAddress}"
            : $"{issue.OwnerAddress} ⇄ {(string.IsNullOrEmpty(issue.TargetAddress) ? PlmModemAddress : issue.TargetAddress)}";

        string targetLabel = !string.IsNullOrEmpty(issue.ControllerName) && !string.IsNullOrEmpty(issue.ResponderName)
            ? $"{issue.ControllerName} ⇄ {issue.ResponderName}"
            : $"{issue.OwnerName} ⇄ {(string.IsNullOrEmpty(issue.TargetName) ? "PLM" : issue.TargetName)}";

        var staged = new StagedChangeModel
        {
            Id = $"FIX-{issue.Id}",
            TargetAddress = targetAddress,
            TargetLabel = targetLabel,
            Operation = issue.RecommendedAction.ToUpperInvariant(),
            CurrentState = $"{issue.IssueTypeBadge}: {issue.Summary}",
            ProposedState = proposedState,
            Description = issue.Explanation,
            Risk = issue.Severity == "error" ? ChangeRisk.Medium : ChangeRisk.Low,
            ExecutionHandler = async () =>
            {
                if (issue.RecommendedAction == "create_reciprocal")
                {
                    var req = new ReciprocalLinkRequestModel
                    {
                        ControllerAddress = issue.ControllerAddress,
                        ResponderAddress = issue.ResponderAddress,
                        Group = issue.Group,
                        Data1 = issue.Data1,
                        Data2 = issue.Data2,
                        Data3 = issue.Data3
                    };
                    return await _apiClient.CreateReciprocalLinkAsync(req);
                }
                else if (issue.RecommendedAction == "create_default_link")
                {
                    string devAddr = issue.OwnerAddress;
                    string plm = PlmModemAddress;
                    var req1 = new ReciprocalLinkRequestModel
                    {
                        ControllerAddress = plm,
                        ResponderAddress = devAddr,
                        Group = 1,
                        Data1 = 255,
                        Data2 = 28,
                        Data3 = 1
                    };
                    var (s1, m1) = await _apiClient.CreateReciprocalLinkAsync(req1);
                    if (!s1) return (false, m1);

                    var req2 = new ReciprocalLinkRequestModel
                    {
                        ControllerAddress = devAddr,
                        ResponderAddress = plm,
                        Group = 1,
                        Data1 = 255,
                        Data2 = 28,
                        Data3 = 1
                    };
                    return await _apiClient.CreateReciprocalLinkAsync(req2);
                }
                else if (issue.RecommendedAction == "delete_record" && !string.IsNullOrEmpty(issue.MemAddr))
                {
                    return await _apiClient.DeleteDeviceAldbRecordAsync(issue.OwnerAddress, issue.MemAddr);
                }
                else if (issue.RecommendedAction == "purge_ghost")
                {
                    return await _apiClient.PurgeGhostLinksAsync(issue.TargetAddress);
                }
                return (false, "Unknown action");
            }
        };

        StagedChanges.Add(staged);
        StatusMessage = $"Staged fix for {issue.Summary}. Review in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Staged fix for {issue.Summary}.");
        SelectedTabIndex = 4;
        CommitStagedChangesCommand?.NotifyCanExecuteChanged();
    }

    public async Task FixAllHalfLinksAsync()
    {
        var halfLinks = NetworkIssues.Where(i => i.IssueType == "half_link" && i.CanAutoFix).ToList();
        if (halfLinks.Count == 0)
        {
            StatusMessage = "No auto-fixable broken half-links found.";
            return;
        }

        var confirm = MessageBox.Show(
            $"Found {halfLinks.Count} broken half-links.\n\nWould you like to auto-repair all of them by generating the missing reciprocal responder records?",
            "Auto-Fix All Half-Links",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question);

        if (confirm != MessageBoxResult.Yes) return;

        IsLoading = true;
        StatusMessage = $"Auto-fixing {halfLinks.Count} broken half-links...";
        AddLog(LogLevel.Info, "Analyzer", $"Beginning batch repair of {halfLinks.Count} broken half-links...");

        int repaired = 0;
        int failed = 0;

        try
        {
            foreach (var hl in halfLinks)
            {
                var req = new ReciprocalLinkRequestModel
                {
                    ControllerAddress = hl.ControllerAddress,
                    ResponderAddress = hl.ResponderAddress,
                    Group = hl.Group,
                    Data1 = hl.Data1,
                    Data2 = hl.Data2,
                    Data3 = hl.Data3
                };
                var (success, _) = await _apiClient.CreateReciprocalLinkAsync(req);
                if (success) repaired++;
                else failed++;
            }

            StatusMessage = $"Batch auto-fix complete: {repaired} repaired, {failed} failed.";
            AddLog(LogLevel.Success, "Analyzer", $"Auto-repaired {repaired} half-links ({failed} failed). Refreshing ALDB...");
            await QueryAldbAsync(silent: true);
        }
        catch (Exception ex)
        {
            StatusMessage = $"Batch fix error: {ex.Message}";
            AddLog(LogLevel.Error, "Analyzer", $"Batch fix failed: {ex.Message}");
        }
        finally
        {
            IsLoading = false;
        }
    }

    public void StageAllAnalyzerFixes()
    {
        var fixable = NetworkIssues.Where(i => i.CanAutoFix || !string.IsNullOrEmpty(i.MemAddr)).ToList();
        if (fixable.Count == 0)
        {
            StatusMessage = "No fixable analyzer issues to stage.";
            return;
        }

        int count = 0;
        foreach (var issue in fixable)
        {
            string stageId = $"FIX-{issue.Id}";
            if (StagedChanges.Any(sc => sc.Id == stageId)) continue;

            StageNetworkIssueFix(issue);
            count++;
        }

        StatusMessage = $"Staged {count} analyzer fixes in Pending Changes.";
        AddLog(LogLevel.Info, "Staging", $"Staged {count} analyzer fixes for network repair.");
        SelectedTabIndex = 4;
    }
}


