namespace GameSpineTool.Services;

/// <summary>引擎脚本与 Python 解释器的位置。</summary>
public static class EnginePaths
{
    public static string EngineDir => Path.Combine(AppContext.BaseDirectory, "engine");

    public static string Python
    {
        get
        {
            var env = Environment.GetEnvironmentVariable("GST_PYTHON");
            if (!string.IsNullOrWhiteSpace(env)) return env;
            var bundled = Path.Combine(AppContext.BaseDirectory, "python", "python.exe");
            return File.Exists(bundled) ? bundled : DevPython() ?? "python.exe";
        }
    }

    /// <summary>开发期从程序集位置向上找项目内的 .venv，便携版不需要。</summary>
    private static string? DevPython()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, ".venv", "Scripts", "python.exe");
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }
}
