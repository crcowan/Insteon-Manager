using System.IO;
using System.Text.Json;
using InsteonManager.Models;

namespace InsteonManager.Services;

public class NodeCoordinate
{
    public double X { get; set; }
    public double Y { get; set; }
}

public class UiSettings
{
    public string BackendUrl { get; set; } = string.Empty;
}

public class NodePositionService
{
    private readonly string _storageFilePath;
    private readonly string _localSettingsFilePath;
    private readonly string _appDataSettingsFilePath;
    private readonly object _lock = new();

    public NodePositionService()
    {
        // Store in local app folder or fallback to user AppData
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string candidatePath = Path.Combine(baseDir, "node_positions.json");

        string appData = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "InsteonManager"
        );
        try
        {
            Directory.CreateDirectory(appData);
        }
        catch
        {
        }
        _appDataSettingsFilePath = Path.Combine(appData, "ui_settings.json");

        try
        {
            // Test write permission
            File.WriteAllText(candidatePath + ".test", "");
            File.Delete(candidatePath + ".test");
            _storageFilePath = candidatePath;
            _localSettingsFilePath = Path.Combine(baseDir, "ui_settings.json");
        }
        catch
        {
            _storageFilePath = Path.Combine(appData, "node_positions.json");
            _localSettingsFilePath = _appDataSettingsFilePath;
        }
    }

    public string LoadBackendUrl(string defaultUrl = "http://localhost:8000")
    {
        lock (_lock)
        {
            foreach (var path in new[] { _localSettingsFilePath, _appDataSettingsFilePath })
            {
                try
                {
                    if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
                    {
                        string json = File.ReadAllText(path);
                        var settings = JsonSerializer.Deserialize<UiSettings>(json);
                        if (settings != null && !string.IsNullOrWhiteSpace(settings.BackendUrl))
                        {
                            return settings.BackendUrl.Trim();
                        }
                    }
                }
                catch
                {
                }
            }
            return defaultUrl;
        }
    }

    public void SaveBackendUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            return;
        }

        lock (_lock)
        {
            var settings = new UiSettings { BackendUrl = url.Trim() };
            string json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });

            foreach (var path in new[] { _localSettingsFilePath, _appDataSettingsFilePath }.Distinct())
            {
                try
                {
                    if (!string.IsNullOrWhiteSpace(path))
                    {
                        File.WriteAllText(path, json);
                    }
                }
                catch
                {
                }
            }
        }
    }

    public Dictionary<string, NodeCoordinate> LoadPositions()
    {
        lock (_lock)
        {
            try
            {
                if (File.Exists(_storageFilePath))
                {
                    string json = File.ReadAllText(_storageFilePath);
                    var dict = JsonSerializer.Deserialize<Dictionary<string, NodeCoordinate>>(json);
                    return dict ?? new Dictionary<string, NodeCoordinate>();
                }
            }
            catch
            {
                // Ignore parse errors and return empty dictionary
            }
            return new Dictionary<string, NodeCoordinate>();
        }
    }

    public void SavePositions(IEnumerable<DeviceNode> nodes)
    {
        lock (_lock)
        {
            try
            {
                var dict = LoadPositions();
                foreach (var node in nodes)
                {
                    if (!string.IsNullOrWhiteSpace(node.Address))
                    {
                        dict[node.Address] = new NodeCoordinate { X = node.X, Y = node.Y };
                    }
                }

                string json = JsonSerializer.Serialize(dict, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_storageFilePath, json);
            }
            catch
            {
                // Silently ignore or log file write failure
            }
        }
    }

    public void SavePosition(string address, double x, double y)
    {
        lock (_lock)
        {
            try
            {
                var dict = LoadPositions();
                dict[address] = new NodeCoordinate { X = x, Y = y };
                string json = JsonSerializer.Serialize(dict, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_storageFilePath, json);
            }
            catch
            {
            }
        }
    }
}
