using System.IO;
using System.Text.Json;
using InsteonManager.Models;

namespace InsteonManager.Services;

public class NodeCoordinate
{
    public double X { get; set; }
    public double Y { get; set; }
}

public class NodePositionService
{
    private readonly string _storageFilePath;
    private readonly object _lock = new();

    public NodePositionService()
    {
        // Store in local app folder or fallback to user AppData
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string candidatePath = Path.Combine(baseDir, "node_positions.json");

        try
        {
            // Test write permission
            File.WriteAllText(candidatePath + ".test", "");
            File.Delete(candidatePath + ".test");
            _storageFilePath = candidatePath;
        }
        catch
        {
            string appData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "InsteonManager"
            );
            Directory.CreateDirectory(appData);
            _storageFilePath = Path.Combine(appData, "node_positions.json");
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
