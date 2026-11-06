using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace GameSpineTool.Services;

public sealed class EngineException : Exception
{
    public EngineException(string kind, string message) : base(message) => Kind = kind;

    public string Kind { get; }
}

/// <summary>与 Python 引擎用 stdio JSON Lines 通信。</summary>
public sealed class EngineClient : IAsyncDisposable
{
    private readonly Process _proc;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> _pending = new();
    private int _nextId;

    public event Action<JsonElement>? EventReceived;

    private EngineClient(Process proc) => _proc = proc;

    public static EngineClient Start(string pythonExe, string engineDir)
    {
        var psi = new ProcessStartInfo
        {
            FileName = pythonExe,
            WorkingDirectory = engineDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardInputEncoding = new UTF8Encoding(false),
        };
        psi.ArgumentList.Add("-u");
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("gst_engine");
        psi.Environment["PYTHONPATH"] = engineDir;
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        var proc = Process.Start(psi) ?? throw new InvalidOperationException("引擎启动失败");
        var client = new EngineClient(proc);
        client.StartReading();
        return client;
    }

    public async Task<JsonElement> CallAsync(string op, object? args = null)
    {
        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;

        var line = JsonSerializer.Serialize(new { id, op, args = args ?? new { } });
        await _writeLock.WaitAsync();
        try
        {
            await _proc.StandardInput.WriteLineAsync(line);
            await _proc.StandardInput.FlushAsync();
        }
        finally
        {
            _writeLock.Release();
        }

        var reply = await tcs.Task;
        if (!reply.GetProperty("ok").GetBoolean())
        {
            var err = reply.GetProperty("error");
            throw new EngineException(
                err.GetProperty("kind").GetString() ?? "Error",
                err.GetProperty("message").GetString() ?? "引擎返回错误");
        }
        return reply.GetProperty("data");
    }

    private void StartReading()
    {
        _ = Task.Run(async () =>
        {
            _ = Task.Run(async () =>
            {
                while (await _proc.StandardError.ReadLineAsync() is { } err)
                {
                    EventReceived?.Invoke(JsonSerializer.SerializeToElement(
                        new { ev = "log", level = "STDERR", text = err }));
                }
            });

            while (await _proc.StandardOutput.ReadLineAsync() is { } line)
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                JsonElement root;
                try
                {
                    using var doc = JsonDocument.Parse(line);
                    root = doc.RootElement.Clone();
                }
                catch (JsonException)
                {
                    continue;
                }

                if (root.TryGetProperty("ev", out _))
                {
                    EventReceived?.Invoke(root);
                    continue;
                }

                if (root.TryGetProperty("id", out var idEl)
                    && idEl.ValueKind == JsonValueKind.Number
                    && _pending.TryRemove(idEl.GetInt32(), out var tcs))
                {
                    tcs.TrySetResult(root);
                }
            }

            foreach (var kv in _pending)
                kv.Value.TrySetException(new EngineException("Exited", "引擎已退出"));
            _pending.Clear();
        });
    }

    public async ValueTask DisposeAsync()
    {
        try { _proc.StandardInput.Close(); } catch { }
        try
        {
            if (!_proc.WaitForExit(2000)) _proc.Kill(entireProcessTree: true);
            await _proc.WaitForExitAsync();
        }
        catch { }
        _proc.Dispose();
    }
}
