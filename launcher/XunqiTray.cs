// Native Windows tray controller for the Xunqi personal app.
// One owned Python server; no boot autostart, no supplier calls on startup.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("寻栖")]
[assembly: AssemblyProduct("寻栖 · 个人版")]
[assembly: AssemblyCompany("Xunqi Personal")]
[assembly: AssemblyVersion("0.9.98.0")]
[assembly: AssemblyFileVersion("0.9.98.0")]

namespace XunqiLauncher
{
    internal static class Names
    {
        internal const string MutexName = @"Local\XunqiPersonalR97Tray";
        internal const string ShowEvent = @"Local\XunqiPersonalR97Show";
        internal const string QuitEvent = @"Local\XunqiPersonalR97Quit";
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            if (args.Length > 0 && args[0] == "--quit")
            {
                Signal(Names.QuitEvent);
                return;
            }
            bool isNew;
            using (Mutex single = new Mutex(true, Names.MutexName, out isNew))
            {
                if (!isNew) { Signal(Names.ShowEvent); return; }
                try
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    Application.Run(new TrayApp());
                }
                catch (Exception error)
                {
                    MessageBox.Show("寻栖启动失败：" + error.Message,
                        "寻栖", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                finally { single.ReleaseMutex(); }
            }
        }

        private static void Signal(string name)
        {
            try
            {
                using (EventWaitHandle eventHandle = EventWaitHandle.OpenExisting(name))
                    eventHandle.Set();
            }
            catch (WaitHandleCannotBeOpenedException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    internal sealed class TrayApp : ApplicationContext
    {
        private readonly string root;
        private readonly string runtime;
        private readonly string marker;
        private readonly string logPath;
        private readonly string edgeProfile;
        private readonly NotifyIcon tray;
        private readonly ToolStripMenuItem statusItem;
        private readonly System.Windows.Forms.Timer timer;
        private readonly EventWaitHandle showEvent;
        private readonly EventWaitHandle quitEvent;
        private readonly object logLock = new object();
        private Process server;
        private Process browser;
        private IntPtr job;
        private bool ready;
        private bool closing;
        private bool failed;
        private bool startRequested;
        private DateTime startTime;
        private StreamWriter logfile;
        private StartupWindow startup;

        // The desktop EXE is an actual binary, not a shortcut. Its build-time
        // resource points to this machine's existing source/Python environment.
        // This does not pretend that a copied EXE bundles Python or API keys.
        private static string LocateProjectRoot()
        {
            string baseDir = Path.GetDirectoryName(Application.ExecutablePath);
            string[] candidates = {
                baseDir,
                Path.GetFullPath(Path.Combine(baseDir, "..", ".."))
            };
            foreach (string path in candidates)
                if (IsProjectRoot(path)) return path;

            using (Stream stream = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("XunqiInstallRoot"))
            {
                if (stream != null)
                {
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8, true))
                    {
                        string location = reader.ReadToEnd().Trim();
                        if (IsProjectRoot(location)) return Path.GetFullPath(location);
                    }
                }
            }
            throw new DirectoryNotFoundException(
                "未找到本机寻栖项目。该 EXE 连接本机已安装的寻栖项目和 Python 环境。" +
                "如果项目移动了位置，请在新的项目目录中重新运行安装脚本。");
        }

        private static bool IsProjectRoot(string candidate)
        {
            if (String.IsNullOrWhiteSpace(candidate) ||
                !Path.IsPathRooted(candidate)) return false;
            return File.Exists(Path.Combine(candidate, "scripts", "tray_server.py")) &&
                File.Exists(Path.Combine(candidate, "backend", "stay_agent", "main.py"));
        }

        internal TrayApp()
        {
            root = LocateProjectRoot();
            runtime = Path.Combine(root, "runtime", "launcher");
            Directory.CreateDirectory(runtime);
            marker = Path.Combine(runtime, "stop-" + Guid.NewGuid().ToString("N") + ".flag");
            edgeProfile = Path.Combine(runtime, "edge-profile");
            logPath = Path.Combine(runtime, "launcher.log");
            if (File.Exists(logPath) && new FileInfo(logPath).Length > 1048576)
            {
                try { File.Copy(logPath, logPath + ".old", true); File.Delete(logPath); }
                catch (IOException) { }
            }
            logfile = new StreamWriter(new FileStream(logPath,
                FileMode.Append, FileAccess.Write, FileShare.ReadWrite), new UTF8Encoding(false));
            logfile.AutoFlush = true;
            Log("Tray started. Root=" + root);

            ContextMenuStrip menu = new ContextMenuStrip();
            ToolStripMenuItem openItem = new ToolStripMenuItem("打开寻栖");
            openItem.Font = new Font(openItem.Font, FontStyle.Bold);
            openItem.Click += delegate { OpenApp(); };
            statusItem = new ToolStripMenuItem("正在启动本地服务…");
            statusItem.Enabled = false;
            ToolStripMenuItem logItem = new ToolStripMenuItem("查看启动日志");
            logItem.Click += delegate { OpenLog(); };
            ToolStripMenuItem quitItem = new ToolStripMenuItem("退出寻栖（关闭后台）");
            quitItem.Click += delegate { ExitThread(); };
            menu.Items.Add(openItem);
            menu.Items.Add(statusItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(logItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(quitItem);

            Icon icon = SystemIcons.Application;
            string iconPath = Path.Combine(root, "launcher", "xunqi.ico");
            try { if (File.Exists(iconPath)) icon = new Icon(iconPath); } catch { }
            startup = new StartupWindow(icon, delegate { ExitThread(); });
            tray = new NotifyIcon();
            tray.Icon = icon;
            tray.ContextMenuStrip = menu;
            tray.Text = "寻栖 · 正在启动";
            tray.Visible = true;
            tray.DoubleClick += delegate { OpenApp(); };

            showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, Names.ShowEvent);
            quitEvent = new EventWaitHandle(false, EventResetMode.AutoReset, Names.QuitEvent);
            timer = new System.Windows.Forms.Timer();
            timer.Interval = 550;
            timer.Tick += OnTick;
            timer.Start();
            // Start only after the WinForms message loop is active, so an
            // occupied port produces one actionable dialog then exits cleanly.
        }

        private void Log(string line)
        {
            lock (logLock)
            {
                try
                {
                    if (logfile != null)
                        logfile.WriteLine(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + line);
                }
                catch (IOException) { }
            }
        }

        private static bool CanBind(int port)
        {
            TcpListener listener = new TcpListener(IPAddress.Loopback, port);
            try { listener.Start(); return true; }
            catch (SocketException) { return false; }
            finally { try { listener.Stop(); } catch { } }
        }

        private void Fail(string message)
        {
            if (closing || failed) return;
            failed = true;
            if (timer != null) timer.Stop();
            if (startup != null) startup.Hide();
            Log("ERROR " + message);
            MessageBox.Show(message + "\n\n日志：" + logPath, "寻栖启动失败",
                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            ExitThread();
        }

        private static string Quoted(string s)
        {
            return "\"" + s.Replace("\"", "\\\"") + "\"";
        }

        private void StartServer()
        {
            string python = @"D:\Anaconda3\envs\xunqi\python.exe";
            if (!File.Exists(python))
            {
                Fail("未找到项目 Python 环境。");
                return;
            }
            if (!File.Exists(Path.Combine(root, "frontend", "dist", "index.html")))
            {
                Fail("未找到网页构建文件，请参照 README 构建前端。");
                return;
            }
            if (!CanBind(8000))
            {
                Fail("端口 8000 已被占用。请先关闭以前从终端或 start.cmd 启动的寻栖，再打开桌面图标。");
                return;
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = python;
                info.Arguments = "-X utf8 scripts\\tray_server.py --port 8000 --exit-marker " + Quoted(marker);
                info.WorkingDirectory = root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                info.EnvironmentVariables["XUNQI_DATA_MODE"] = "live";
                info.EnvironmentVariables["XUNQI_HOTEL_SOURCE"] = "rollinggo";
                job = Native.CreateKillOnCloseJob();
                server = new Process();
                server.StartInfo = info;
                server.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data != null) Log("server: " + e.Data);
                };
                server.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data != null) Log("server: " + e.Data);
                };
                if (!server.Start()) throw new InvalidOperationException("进程启动失败");
                if (job != IntPtr.Zero && !Native.AssignProcessToJobObject(job, server.Handle))
                {
                    Log("Job unavailable; direct-process cleanup fallback.");
                    Native.CloseHandle(job);
                    job = IntPtr.Zero;
                }
                server.BeginOutputReadLine();
                server.BeginErrorReadLine();
                startTime = DateTime.UtcNow;
                Log("Owned server PID=" + server.Id);
            }
            catch (Exception e) { Fail("本机服务启动失败：" + e.Message); }
        }

        private void OnTick(object sender, EventArgs e)
        {
            if (closing || failed) return;
            if (!startRequested)
            {
                startRequested = true;
                startup.Show();
                startup.SetStatus("正在启动本地服务，稍后自动打开窗口…");
                StartServer();
                return;
            }
            if (quitEvent.WaitOne(0)) { ExitThread(); return; }
            if (showEvent.WaitOne(0))
            {
                if (ready) OpenApp();
                else if (startup != null) { startup.Show(); startup.Activate(); }
            }
            if (ready) return;
            if (server == null || server.HasExited)
            {
                Fail("本机服务意外退出。");
                return;
            }
            if (Healthy())
            {
                ready = true;
                tray.Text = "寻栖 · 后台运行中";
                statusItem.Text = "本地服务运行正常 · 8000";
                Log("Health verified.");
                if (startup != null)
                {
                    startup.Close();
                    startup.Dispose();
                    startup = null;
                }
                try { tray.ShowBalloonTip(2500, "寻栖已启动",
                    "关闭窗口后可继续在托盘运行；右键图标选「退出寻栖」即可关闭后台。",
                    ToolTipIcon.Info); } catch { }
                OpenApp();
                return;
            }
            if ((DateTime.UtcNow - startTime).TotalSeconds > 30)
                Fail("本机服务启动超时，请查看启动日志。");
        }

        private static bool Healthy()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(
                    "http://127.0.0.1:8000/api/health");
                request.Timeout = 600;
                request.ReadWriteTimeout = 600;
                request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader stream = new StreamReader(response.GetResponseStream()))
                {
                    string body = stream.ReadToEnd();
                    return response.StatusCode == HttpStatusCode.OK
                        && body.Contains("\"status\":\"ok\"")
                        && body.Contains("\"data_mode\":\"live\"")
                        && body.Contains("\"provider\":\"rollinggo\"");
                }
            }
            catch { return false; }
        }

        private static string EdgePath()
        {
            string[] paths = {
                @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                @"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
            };
            foreach (string path in paths)
                if (File.Exists(path)) return path;
            return null;
        }

        private void OpenApp()
        {
            if (closing || !ready) return;
            try
            {
                if (browser != null && !browser.HasExited)
                {
                    browser.Refresh();
                    IntPtr handle = browser.MainWindowHandle;
                    if (handle != IntPtr.Zero)
                    {
                        Native.ShowWindow(handle, 9);
                        Native.SetForegroundWindow(handle);
                        return;
                    }
                }
                string edge = EdgePath();
                if (edge != null)
                {
                    Directory.CreateDirectory(edgeProfile);
                    ProcessStartInfo info = new ProcessStartInfo();
                    info.FileName = edge;
                    info.Arguments = "--app=http://127.0.0.1:8000/ --user-data-dir="
                        + Quoted(edgeProfile)
                        + " --no-first-run --no-default-browser-check --disable-background-mode"
                        + " --window-size=1160,850";
                    info.WorkingDirectory = root;
                    info.UseShellExecute = false;
                    browser = Process.Start(info);
                    Log("Opened dedicated Edge app window.");
                }
                else
                {
                    Process.Start(new ProcessStartInfo("http://127.0.0.1:8000/")
                        { UseShellExecute = true });
                    Log("Opened default browser.");
                }
            }
            catch (Exception error)
            {
                Log("Browser error: " + error.Message);
                MessageBox.Show("窗口打开失败，可直接访问 http://127.0.0.1:8000/\n"
                    + error.Message, "寻栖", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void OpenLog()
        {
            try { Process.Start("notepad.exe", Quoted(logPath)); }
            catch (Exception e) { Log("Notepad error: " + e.Message); }
        }

        protected override void ExitThreadCore()
        {
            if (closing) return;
            closing = true;
            timer.Stop();
            tray.Visible = false;
            if (startup != null)
            {
                startup.Close();
                startup.Dispose();
                startup = null;
            }
            try
            {
                if (browser != null && !browser.HasExited)
                    browser.CloseMainWindow();
            }
            catch { }
            if (server != null)
            {
                try
                {
                    if (!server.HasExited)
                    {
                        Log("Requesting graceful shutdown.");
                        File.WriteAllText(marker, "exit", Encoding.ASCII);
                        if (!server.WaitForExit(5000))
                            Log("Server shutdown timeout; closing job.");
                    }
                }
                catch (Exception error) { Log("Stop warning: " + error.Message); }
            }
            if (job != IntPtr.Zero)
            {
                Native.CloseHandle(job);
                job = IntPtr.Zero;
            }
            try { if (server != null && !server.HasExited) server.Kill(); } catch { }
            try { if (File.Exists(marker)) File.Delete(marker); } catch { }
            Log("Tray stopped.");
            try { if (logfile != null) { logfile.Dispose(); logfile = null; } } catch { }
            tray.Dispose();
            timer.Dispose();
            showEvent.Dispose();
            quitEvent.Dispose();
            if (server != null) server.Dispose();
            if (browser != null) browser.Dispose();
            base.ExitThreadCore();
        }
    }

    internal static class Native
    {
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
        private const int JobObjectExtendedLimitInformation = 9;
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimit
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct Counters
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimit
        {
            public BasicLimit BasicLimitInformation;
            public Counters IoInfo;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job, int infoClass, IntPtr value, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);
        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr handle);
        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr handle, int command);

        public static IntPtr CreateKillOnCloseJob()
        {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) return job;
            ExtendedLimit limit = new ExtendedLimit();
            limit.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int size = Marshal.SizeOf(typeof(ExtendedLimit));
            IntPtr memory = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(limit, memory, false);
                if (SetInformationJobObject(
                    job, JobObjectExtendedLimitInformation, memory, (uint)size))
                    return job;
            }
            finally { Marshal.FreeHGlobal(memory); }
            CloseHandle(job);
            return IntPtr.Zero;
        }
    }
}
