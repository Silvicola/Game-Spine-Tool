using System.Text.Json;
using System.Text.Json.Serialization;

namespace GameSpineTool.Services;

public sealed class PhaseTiming
{
    public string Name { get; set; } = "";
    public double Seconds { get; set; }
}

public sealed class RunRecord
{
    public string Id { get; set; } = "";
    public string StartedAt { get; set; } = "";
    public string FinishedAt { get; set; } = "";
    public double ElapsedSeconds { get; set; }
    public string Game { get; set; } = "";
    public string OutputDir { get; set; } = "";
    public string Version { get; set; } = "";
    public bool Success { get; set; }
    public int Groups { get; set; }
    public int Files { get; set; }
    public int SkippedGroups { get; set; }
    public List<string> Errors { get; set; } = new();
    public List<PhaseTiming> Phases { get; set; } = new();
    public string Log { get; set; } = "";

    [JsonIgnore]
    public string Title => DateTime.TryParse(StartedAt, out var t)
        ? t.ToString("MM-dd HH:mm") : StartedAt;

    [JsonIgnore]
    public string Subtitle => $"{Groups} 组 · {(Success ? "成功" : "失败")}"
        + (SkippedGroups > 0 ? $" · 跳过 {SkippedGroups} 组" : "");
}

/// <summary>运行历史落盘。一次运行一个 JSON，避免并发写与文件锁。</summary>
public static class HistoryStore
{
    private const int KeepCount = 50;

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
    };

    /// <summary>历史随程序目录走，便携版每份解压副本互相独立。</summary>
    public static string DirectoryPath => Path.Combine(AppContext.BaseDirectory, "history");

    public static void Save(RunRecord record)
    {
        Directory.CreateDirectory(DirectoryPath);
        var path = Path.Combine(DirectoryPath, record.Id + ".json");
        File.WriteAllText(path, JsonSerializer.Serialize(record, Options));

        var files = new DirectoryInfo(DirectoryPath)
            .GetFiles("*.json")
            .OrderByDescending(f => f.Name)
            .Skip(KeepCount);
        foreach (var old in files)
        {
            try { old.Delete(); } catch { }
        }
    }

    public static List<RunRecord> Load()
    {
        if (!Directory.Exists(DirectoryPath)) return new();
        var result = new List<RunRecord>();
        foreach (var file in new DirectoryInfo(DirectoryPath)
                     .GetFiles("*.json")
                     .OrderByDescending(f => f.Name))
        {
            try
            {
                var record = JsonSerializer.Deserialize<RunRecord>(
                    File.ReadAllText(file.FullName), Options);
                if (record is not null) result.Add(record);
            }
            catch
            {
                // 单个坏文件不影响其余历史
            }
        }
        return result;
    }
}
