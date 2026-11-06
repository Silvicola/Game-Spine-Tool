using System.Text;
using System.Text.Json;
using GameSpineTool.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace GameSpineTool;

public sealed partial class MainWindow : Window
{
    private readonly StringBuilder _log = new();
    private readonly StringBuilder _runLog = new();
    private readonly List<string> _runErrors = new();
    private readonly DispatcherQueue _dispatcher = DispatcherQueue.GetForCurrentThread();
    private DateTime _runStartedAt;
    private bool _inRun;

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;

        var assets = Path.Combine(AppContext.BaseDirectory, "Assets");
        var iconPath = Path.Combine(assets, "AppIcon.ico");
        if (File.Exists(iconPath))
        {
            AppWindow.SetIcon(iconPath);
        }

        if (Content is FrameworkElement root)
        {
            root.Loaded += (_, _) =>
            {
                var scale = root.XamlRoot?.RasterizationScale ?? 1.0;
                var area = DisplayArea.GetFromWindowId(AppWindow.Id,
                    DisplayAreaFallback.Primary).WorkArea;
                var w = Math.Min((int)(900 * scale), area.Width);
                var h = Math.Min((int)(640 * scale), area.Height);
                AppWindow.MoveAndResize(new RectInt32(
                    area.X + (area.Width - w) / 2,
                    area.Y + (area.Height - h) / 2,
                    w, h));
            };
        }

        if (App.Engine is { } engine)
        {
            engine.EventReceived += OnEngineEvent;
        }
        else
        {
            AppendLog("ERROR", App.EngineError ?? "引擎未启动");
        }
    }

    private void OnEngineEvent(JsonElement ev)
    {
        var kind = ev.GetProperty("ev").GetString();
        _dispatcher.TryEnqueue(() =>
        {
            switch (kind)
            {
                case "phase":
                    var index = ev.GetProperty("index").GetInt32();
                    var total = ev.GetProperty("total").GetInt32();
                    var phaseName = ev.GetProperty("name").GetString() ?? "";
                    StageText.Text = phaseName;
                    StageProgress.Value = (double)index / total * 100;
                    StageDetail.Text = $"阶段 {index} / {total}";
                    break;
                case "log":
                    AppendLog(
                        ev.GetProperty("level").GetString() ?? "INFO",
                        ev.GetProperty("text").GetString() ?? "");
                    break;
                case "progress":
                    var done = ev.GetProperty("done").GetInt32();
                    var all = ev.GetProperty("total").GetInt32();
                    var label = ev.TryGetProperty("label", out var l)
                        ? (l.GetString() ?? "进度")
                        : "进度";
                    StageDetail.Text = $"{label} {done} / {all}";
                    break;
            }
        });
    }

    private void AppendLog(string level, string text)
    {
        var line = level is "WARNING" or "ERROR" or "STDERR"
            ? $"[{level}] {text}"
            : text;
        _log.AppendLine(line);
        LogText.Text = _log.ToString();
        if (_inRun)
        {
            _runLog.AppendLine(line);
            if (level is "WARNING" or "ERROR" or "STDERR") _runErrors.Add(line);
        }
    }

    private async void StartButton_Click(object sender, RoutedEventArgs e)
    {
        if (App.Engine is null)
        {
            StageText.Text = "引擎未启动";
            StageDetail.Text = App.EngineError ?? "找不到 Python 运行环境";
            return;
        }

        if (string.IsNullOrWhiteSpace(OutputTextBox.Text))
        {
            StageText.Text = "请先选择输出目录";
            StageDetail.Text = "点击「浏览」选择整理产物的落点";
            return;
        }

        StartButton.IsEnabled = false;
        StageProgress.Value = 0;

        _runLog.Clear();
        _runErrors.Clear();
        _runStartedAt = DateTime.Now;
        _inRun = true;

        var success = false;
        var version = "";
        var groups = 0;
        var files = 0;
        var skippedGroups = 0;
        JsonElement phaseTimings = default;
        try
        {
            var autoUpscale = UpscaleSwitch.IsOn;
            var run = await App.Engine.CallAsync("run", new
            {
                output_dir = OutputTextBox.Text,
                upscale = autoUpscale,
            });
            success = true;
            version = run.TryGetProperty("version", out var v) ? v.GetString() ?? "" : "";
            groups = run.TryGetProperty("groups", out var g) ? g.GetInt32() : 0;
            files = run.TryGetProperty("files", out var f) ? f.GetInt32() : 0;
            skippedGroups = run.TryGetProperty("skipped_groups", out var sg)
                ? sg.GetInt32() : 0;
            phaseTimings = run.TryGetProperty("phase_timings", out var pt)
                ? pt.Clone() : default;

            if (!autoUpscale)
            {
                var candidates = run.GetProperty("upscale_candidates");
                var pages = candidates.GetProperty("pages").GetInt32();
                var candidateGroups = candidates.GetProperty("groups").GetInt32();
                if (pages > 0)
                {
                    var dialog = new ContentDialog
                    {
                        Title = "发现可超分的模型",
                        Content = $"本次整理有 {candidateGroups} 组共 {pages} 页贴图满足超分条件。" +
                                  "是否放大 2 倍并另存到 Upscaled 目录？原模型不会被修改。",
                        PrimaryButtonText = "开始超分",
                        CloseButtonText = "跳过",
                        DefaultButton = ContentDialogButton.Close,
                        XamlRoot = Content.XamlRoot,
                    };
                    var choice = await dialog.ShowAsync();
                    if (choice == ContentDialogResult.Primary)
                    {
                        await App.Engine.CallAsync("upscale",
                            new { output_dir = OutputTextBox.Text });
                    }
                    else
                    {
                        AppendLog("INFO", "已跳过超分");
                    }
                }
            }
        }
        catch (Exception ex)
        {
            StageText.Text = "失败";
            AppendLog("ERROR", ex.Message);
        }
        finally
        {
            _inRun = false;
            StartButton.IsEnabled = true;
            SaveRunRecord(success, version, groups, files, skippedGroups, phaseTimings);
        }
    }

    private void SaveRunRecord(bool success, string version, int groups, int files,
                               int skippedGroups, JsonElement phaseTimings)
    {
        var finished = DateTime.Now;
        var phases = new List<PhaseTiming>();
        if (phaseTimings.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in phaseTimings.EnumerateArray())
            {
                phases.Add(new PhaseTiming
                {
                    Name = item.TryGetProperty("name", out var n) ? n.GetString() ?? "" : "",
                    Seconds = item.TryGetProperty("seconds", out var s) ? s.GetDouble() : 0,
                });
            }
        }

        try
        {
            HistoryStore.Save(new RunRecord
            {
                Id = _runStartedAt.ToString("yyyyMMdd-HHmmss-fff"),
                StartedAt = _runStartedAt.ToString("yyyy-MM-dd HH:mm:ss"),
                FinishedAt = finished.ToString("yyyy-MM-dd HH:mm:ss"),
                ElapsedSeconds = Math.Round((finished - _runStartedAt).TotalSeconds, 1),
                Game = (GameComboBox.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "",
                OutputDir = OutputTextBox.Text,
                Version = version,
                Success = success,
                Groups = groups,
                Files = files,
                SkippedGroups = skippedGroups,
                Errors = new List<string>(_runErrors),
                Phases = phases,
                Log = _runLog.ToString(),
            });
        }
        catch (Exception ex)
        {
            AppendLog("ERROR", $"运行历史保存失败: {ex.Message}");
        }
    }

    private async void BrowseButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new Windows.Storage.Pickers.FolderPicker
        {
            SuggestedStartLocation = Windows.Storage.Pickers.PickerLocationId.ComputerFolder,
        };
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        var folder = await picker.PickSingleFolderAsync();
        if (folder is not null)
        {
            OutputTextBox.Text = folder.Path;
        }
    }

    private void HistoryFlyout_Opening(object sender, object e)
    {
        try
        {
            var records = HistoryStore.Load();
            HistoryList.ItemsSource = records;
            var empty = records.Count == 0;
            HistoryEmpty.Visibility = empty ? Visibility.Visible : Visibility.Collapsed;
            HistoryDetail.Visibility = empty ? Visibility.Collapsed : Visibility.Visible;
            if (!empty) HistoryList.SelectedIndex = 0;
        }
        catch (Exception ex)
        {
            AppendLog("ERROR", $"读取运行历史失败: {ex.Message}");
        }
    }

    private void HistoryList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (HistoryList.SelectedItem is not RunRecord record) return;

        HistorySuccessDot.Visibility = record.Success ? Visibility.Visible : Visibility.Collapsed;
        HistoryFailDot.Visibility = record.Success ? Visibility.Collapsed : Visibility.Visible;

        HistoryMeta.Text =
            $"{record.StartedAt} · 用时 {record.ElapsedSeconds:F1}s\n" +
            $"{record.Game} · 版本 {record.Version}\n" +
            $"输出目录 {record.OutputDir}\n" +
            $"{record.Groups} 组 / {record.Files} 个文件 · {(record.Success ? "成功" : "失败")}"
            + (record.SkippedGroups > 0 ? $" · 跳过 {record.SkippedGroups} 组已完成" : "");

        HistoryPhases.Text = record.Phases.Count == 0
            ? "阶段耗时：无记录"
            : "阶段耗时  " + string.Join(" / ",
                record.Phases.Select(p => $"{p.Name} {p.Seconds:F1}s"));

        HistoryErrors.Text = $"报错 {record.Errors.Count} 条";
        HistoryLog.Text = record.Log;
    }

    private void CopyHistoryLogButton_Click(object sender, RoutedEventArgs e)
    {
        if (HistoryList.SelectedItem is not RunRecord record) return;
        try
        {
            var package = new Windows.ApplicationModel.DataTransfer.DataPackage();
            package.SetText(record.Log);
            Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(package);
        }
        catch (Exception ex)
        {
            AppendLog("ERROR", $"复制失败: {ex.Message}");
        }
    }

    private void CopyLogButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var package = new Windows.ApplicationModel.DataTransfer.DataPackage();
            package.SetText(_log.ToString());
            Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(package);
            AppendLog("INFO", "日志已复制到剪贴板");
        }
        catch (Exception ex)
        {
            AppendLog("ERROR", $"复制失败: {ex.Message}");
        }
    }
}
