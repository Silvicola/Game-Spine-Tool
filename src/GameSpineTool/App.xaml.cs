using GameSpineTool.Services;
using Microsoft.UI.Xaml;

namespace GameSpineTool;

public partial class App : Application
{
    private Window? _window;

    public static EngineClient? Engine { get; private set; }

    public static string? EngineError { get; private set; }

    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            Engine = EngineClient.Start(EnginePaths.Python, EnginePaths.EngineDir);
        }
        catch (Exception ex)
        {
            EngineError = ex.Message;
        }

        _window = new MainWindow();
        _window.Closed += async (_, _) =>
        {
            if (Engine is { } engine) await engine.DisposeAsync();
        };
        _window.Activate();
    }
}
