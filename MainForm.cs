using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace WhatsAppMigrator
{
    public class MainForm : Form
    {
        // عناصر التحكم الرئيسية
        private readonly ComboBox _directionCombo = new();
        private readonly ComboBox _waTypeCombo = new();
        private readonly TextBox _hexKeyBox = new();
        private readonly TextBox _androidDbBox = new();
        private readonly TextBox _androidMediaBox = new();
        private readonly TextBox _iosBackupBox = new();
        private readonly TextBox _outputDbBox = new();
        private readonly TextBox _referenceCryptBox = new();
        private readonly NeonProgressBar _progress = new();
        private readonly RichTextBox _logBox = new();
        private readonly NeonButton _startBtn = new();
        private readonly NeonButton _cancelBtn = new();
        private readonly Label _statusLabel = new();

        private Process? _engineProcess;
        private bool _isRunning;

        public MainForm()
        {
            Text = "WhatsApp Migrator — Bi-Directional (Android ↔ iOS)";
            Width = 1180;
            Height = 820;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Theme.Background;
            ForeColor = Theme.TextPrimary;
            Font = Theme.LabelFont;
            MinimumSize = new Size(1000, 720);

            BuildUI();
        }

        private void BuildUI()
        {
            // ===== Header =====
            var header = new Label
            {
                Text = "⚡ WhatsApp Migration Engine",
                Font = new Font("Segoe UI", 20f, FontStyle.Bold),
                ForeColor = Theme.NeonGreen,
                Dock = DockStyle.Top,
                Height = 56,
                TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(18, 0, 0, 0),
                BackColor = Theme.Surface,
            };
            Controls.Add(header);

            var subtitle = new Label
            {
                Text = "نقل آمن ثنائي الاتجاه للمحادثات والوسائط — بدون فورمات",
                Font = new Font("Segoe UI", 9f, FontStyle.Italic),
                ForeColor = Theme.TextSecondary,
                Dock = DockStyle.Top,
                Height = 26,
                TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(20, 0, 0, 0),
                BackColor = Theme.Surface,
            };
            Controls.Add(subtitle);

            // ===== الإعدادات العامة =====
            var generalPanel = new NeonPanel
            {
                BorderColor = Theme.NeonCyan,
                Dock = DockStyle.Top,
                Height = 130,
                Margin = new Padding(0),
            };
            Controls.Add(generalPanel);

            var lblDirection = new Label
            {
                Text = "اتجاه النقل:",
                Left = 20, Top = 22, Width = 110,
                ForeColor = Theme.NeonCyan,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold)
            };
            generalPanel.Controls.Add(lblDirection);

            _directionCombo.Left = 140; _directionCombo.Top = 18;
            _directionCombo.Width = 300; _directionCombo.DropDownStyle = ComboBoxStyle.DropDownList;
            _directionCombo.BackColor = Theme.SurfaceAlt; _directionCombo.ForeColor = Theme.TextPrimary;
            _directionCombo.FlatStyle = FlatStyle.Flat;
            _directionCombo.Items.AddRange(new object[] {
                "أندرويد ← آيفون (Android → iOS)",
                "آيفون ← أندرويد (iOS → Android)"
            });
            _directionCombo.SelectedIndex = 0;
            _directionCombo.SelectedIndexChanged += (_, _) => UpdateVisibility();
            generalPanel.Controls.Add(_directionCombo);

            var lblWa = new Label
            {
                Text = "نوع واتساب:",
                Left = 470, Top = 22, Width = 100,
                ForeColor = Theme.NeonPurple,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold)
            };
            generalPanel.Controls.Add(lblWa);

            _waTypeCombo.Left = 580; _waTypeCombo.Top = 18;
            _waTypeCombo.Width = 220; _waTypeCombo.DropDownStyle = ComboBoxStyle.DropDownList;
            _waTypeCombo.BackColor = Theme.SurfaceAlt; _waTypeCombo.ForeColor = Theme.TextPrimary;
            _waTypeCombo.FlatStyle = FlatStyle.Flat;
            _waTypeCombo.Items.AddRange(new object[] { "واتساب العادي", "واتساب الأعمال" });
            _waTypeCombo.SelectedIndex = 0;
            generalPanel.Controls.Add(_waTypeCombo);

            var lblKey = new Label
            {
                Text = "مفتاح hex (64):",
                Left = 20, Top = 70, Width = 130,
                ForeColor = Theme.NeonAmber,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold)
            };
            generalPanel.Controls.Add(lblKey);

            _hexKeyBox.Left = 160; _hexKeyBox.Top = 66;
            _hexKeyBox.Width = 640;
            _hexKeyBox.BackColor = Theme.SurfaceAlt; _hexKeyBox.ForeColor = Theme.NeonAmber;
            _hexKeyBox.BorderStyle = BorderStyle.FixedSingle;
            _hexKeyBox.Font = Theme.MonoFont;
            generalPanel.Controls.Add(_hexKeyBox);

            // ===== مسارات الإدخال/الإخراج =====
            var pathsPanel = new NeonPanel
            {
                BorderColor = Theme.NeonPink,
                Dock = DockStyle.Top,
                Height = 260,
            };
            Controls.Add(pathsPanel);

            AddPathRow(pathsPanel, "قاعدة بيانات أندرويد:", ref _androidDbBox, 20, false, "اختر msgstore.db أو crypt15...");
            AddPathRow(pathsPanel, "مجلد ميديا أندرويد:", ref _androidMediaBox, 62, true, "اختر مجلد Media...");
            AddPathRow(pathsPanel, "نسخة iOS الاحتياطية:", ref _iosBackupBox, 104, true, "اختر مجلد Backup...");
            AddPathRow(pathsPanel, "مسار الإخراج (msgstore.db):", ref _outputDbBox, 146, false, "اختر مكان حفظ msgstore.db...");
            AddPathRow(pathsPanel, "ملف crypt15 مرجعي (اختياري):", ref _referenceCryptBox, 188, false, "اختر crypt15 مرجعي...");

            // ===== الأزرار والحالة =====
            var actionPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 70,
                BackColor = Theme.Background,
            };
            Controls.Add(actionPanel);

            _startBtn.Text = "▶  بدء النقل";
            _startBtn.Left = 20; _startBtn.Top = 14; _startBtn.Width = 220;
            _startBtn.NeonColor = Theme.NeonGreen;
            _startBtn.Click += async (_, _) => await StartMigrationAsync();
            actionPanel.Controls.Add(_startBtn);

            _cancelBtn.Text = "✕ إلغاء";
            _cancelBtn.Left = 260; _cancelBtn.Top = 14; _cancelBtn.Width = 140;
            _cancelBtn.NeonColor = Theme.NeonRed;
            _cancelBtn.Enabled = false;
            _cancelBtn.Click += (_, _) => CancelMigration();
            actionPanel.Controls.Add(_cancelBtn);

            _statusLabel.Left = 420; _statusLabel.Top = 22;
            _statusLabel.Width = 700; _statusLabel.Height = 26;
            _statusLabel.Text = "جاهز.";
            _statusLabel.ForeColor = Theme.NeonCyan;
            _statusLabel.Font = new Font("Segoe UI", 10f, FontStyle.Bold);
            actionPanel.Controls.Add(_statusLabel);

            // ===== شريط التقدم =====
            var progressPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 30, BackColor = Theme.Background,
            };
            Controls.Add(progressPanel);
            _progress.Left = 20; _progress.Top = 8;
            _progress.Width = 1120;
            _progress.BarColor = Theme.NeonGreen;
            progressPanel.Controls.Add(_progress);

            // ===== سجل الإخراج =====
            var logPanel = new NeonPanel
            {
                BorderColor = Theme.NeonCyan,
                Dock = DockStyle.Fill,
            };
            Controls.Add(logPanel);
            _logBox.Dock = DockStyle.Fill;
            _logBox.BackColor = Color.Black;
            _logBox.ForeColor = Theme.NeonGreen;
            _logBox.Font = Theme.MonoFont;
            _logBox.ReadOnly = true;
            _logBox.BorderStyle = BorderStyle.None;
            _logBox.WordWrap = false;
            _logBox.ScrollBars = RichTextBoxScrollBars.Both;
            logPanel.Controls.Add(_logBox);

            UpdateVisibility();
        }

        private void AddPathRow(NeonPanel parent, string labelText, ref TextBox box,
                                 int top, bool isFolder, string placeholder)
        {
            var lbl = new Label
            {
                Text = labelText, Left = 20, Top = top + 4, Width = 220,
                ForeColor = Theme.TextSecondary, Font = Theme.LabelFont,
            };
            parent.Controls.Add(lbl);

            box.Left = 250; box.Top = top; box.Width = 760;
            box.BackColor = Theme.SurfaceAlt; box.ForeColor = Theme.TextPrimary;
            box.BorderStyle = BorderStyle.FixedSingle;
            box.Font = Theme.MonoFont;
            box.PlaceholderText = placeholder;
            parent.Controls.Add(box);

            var btn = new NeonButton
            {
                Text = "⋯", Left = 1020, Top = top, Width = 42, Height = 26,
                NeonColor = Theme.NeonCyan,
            };
            btn.Click += (_, _) =>
            {
                if (isFolder)
                {
                    using var dlg = new FolderBrowserDialog();
                    if (dlg.ShowDialog() == DialogResult.OK) box.Text = dlg.SelectedPath;
                }
                else
                {
                    using var dlg = new OpenFileDialog();
                    if (dlg.ShowDialog() == DialogResult.OK) box.Text = dlg.FileName;
                }
            };
            parent.Controls.Add(btn);
        }

        private void UpdateVisibility()
        {
            bool androidToIos = _directionCombo.SelectedIndex == 0;
            // Android → iOS: يحتاج android_db, android_media, ios_backup
            // iOS → Android: يحتاج ios_backup, output_db, (اختياري) reference_crypt
            _androidDbBox.Enabled = androidToIos;
            _androidMediaBox.Enabled = androidToIos;
            _outputDbBox.Enabled = !androidToIos;
            _referenceCryptBox.Enabled = !androidToIos;
        }

        // ---------- تشغيل المحرك ----------

        private string LocateEngine()
        {
            // 1) بجانب EXE (بعد التغليف بـ PyInstaller)
            var exePath = Path.Combine(AppContext.BaseDirectory, "main_engine.exe");
            if (File.Exists(exePath)) return exePath;

            // 2) مسار تطوير محلي
            var devPath = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..",
                                        "main_engine.py");
            if (File.Exists(devPath)) return "python \"" + Path.GetFullPath(devPath) + "\"";

            throw new FileNotFoundException(
                "لم يتم العثور على main_engine.exe بجانب التطبيق ولا main_engine.py في مسار التطوير.");
        }

        private async Task StartMigrationAsync()
        {
            if (_isRunning) return;

            // تحقق من المدخلات
            if (_hexKeyBox.Text.Length != 64)
            {
                MessageBox.Show("المفتاح يجب أن يكون 64 حرف hex.", "خطأ في المفتاح",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            string engineCmd = LocateEngine();
            string args = BuildArguments();
            _logBox.Clear();
            AppendLog($"> {engineCmd} {args}\n");

            _isRunning = true;
            _startBtn.Enabled = false;
            _cancelBtn.Enabled = true;
            _progress.Value = 0;
            SetStatus("جارٍ التشغيل...", Theme.NeonAmber);

            try
            {
                await RunEngineAsync(engineCmd, args);
            }
            catch (Exception ex)
            {
                AppendLog($"\n[EXCEPTION] {ex.Message}\n");
                SetStatus("فشل: " + ex.Message, Theme.NeonRed);
                _progress.Value = 0;
            }
            finally
            {
                _isRunning = false;
                _startBtn.Enabled = true;
                _cancelBtn.Enabled = false;
                _engineProcess = null;
            }
        }

        private string BuildArguments()
        {
            var sb = new StringBuilder();
            bool androidToIos = _directionCombo.SelectedIndex == 0;
            sb.Append("--direction ");
            sb.Append(androidToIos ? "android_to_ios" : "ios_to_android");
            sb.Append(" --whatsapp-type ");
            sb.Append(_waTypeCombo.SelectedIndex == 1 ? "business" : "regular");
            sb.Append(" --hex-key ");
            sb.Append(Quote(_hexKeyBox.Text.Trim()));

            if (androidToIos)
            {
                sb.Append(" --android-db ").Append(Quote(_androidDbBox.Text.Trim()));
                sb.Append(" --android-media ").Append(Quote(_androidMediaBox.Text.Trim()));
                sb.Append(" --ios-backup ").Append(Quote(_iosBackupBox.Text.Trim()));
            }
            else
            {
                sb.Append(" --ios-backup ").Append(Quote(_iosBackupBox.Text.Trim()));
                sb.Append(" --output-db ").Append(Quote(_outputDbBox.Text.Trim()));
                if (!string.IsNullOrWhiteSpace(_referenceCryptBox.Text))
                    sb.Append(" --reference-crypt ").Append(Quote(_referenceCryptBox.Text.Trim()));
            }
            return sb.ToString();
        }

        private static string Quote(string s) =>
            string.IsNullOrEmpty(s) ? "\"\"" : "\"" + s.Replace("\"", "\\\"") + "\"";

        private async Task RunEngineAsync(string commandLine, string arguments)
        {
            var psi = new ProcessStartInfo
            {
                FileName = ResolveExecutable(commandLine, out string finalArgs),
                Arguments = finalArgs == null ? arguments : finalArgs + " " + arguments,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            // إذا كان الأمر python "path" - نحتاج معالجة خاصة
            if (commandLine.StartsWith("python"))
            {
                psi.FileName = "python";
                var scriptPath = commandLine.Replace("python", "").Trim().Trim('"');
                psi.Arguments = $"\"{scriptPath}\" {arguments}";
            }

            _engineProcess = new Process { StartInfo = psi, EnableRaisingEvents = true };

            var tcs = new TaskCompletionSource<int>();
            _engineProcess.Exited += (_, _) => tcs.TrySetResult(_engineProcess.ExitCode);
            _engineProcess.OutputDataReceived += (_, e) => { if (e.Data != null) HandleOutputLine(e.Data); };
            _engineProcess.ErrorDataReceived += (_, e) => { if (e.Data != null) AppendLog("[STDERR] " + e.Data + "\n"); };

            _engineProcess.Start();
            _engineProcess.BeginOutputReadLine();
            _engineProcess.BeginErrorReadLine();

            int exitCode = await tcs.Task;
            AppendLog($"\n[محرك بايثون انتهى بكود {exitCode}]\n");
            if (exitCode == 0)
            {
                SetStatus("اكتملت العملية بنجاح ✔", Theme.NeonGreen);
                _progress.Value = 100;
            }
            else
            {
                SetStatus($"فشل المحرك (كود {exitCode})", Theme.NeonRed);
            }
        }

        private static string ResolveExecutable(string cmd, out string? modifiedArgs)
        {
            modifiedArgs = null;
            return cmd;
        }

        private void HandleOutputLine(string line)
        {
            const string prefix = "::PROGRESS::";
            if (line.StartsWith(prefix))
            {
                try
                {
                    var doc = JsonDocument.Parse(line.Substring(prefix.Length));
                    var root = doc.RootElement;
                    string type = root.GetProperty("type").GetString() ?? "";
                    string msg = root.TryGetProperty("message", out var m) ? (m.GetString() ?? "") : "";

                    switch (type)
                    {
                        case "log":
                            AppendLog($"[{root.GetProperty("level").GetString()}] {msg}\n");
                            break;
                        case "step":
                            int pct = root.TryGetProperty("percent", out var p) ? p.GetInt32() : 0;
                            _progress.Value = pct;
                            SetStatus(msg, Theme.NeonCyan);
                            AppendLog($"• {msg}  ({pct}%)\n");
                            break;
                        case "success":
                            AppendLog($"✔ {msg}\n");
                            SetStatus(msg, Theme.NeonGreen);
                            _progress.Value = 100;
                            break;
                        case "error":
                            AppendLog($"✖ {msg}\n");
                            if (root.TryGetProperty("detail", out var d))
                                AppendLog(d.GetString() + "\n");
                            SetStatus("خطأ: " + msg, Theme.NeonRed);
                            break;
                    }
                }
                catch (Exception ex)
                {
                    AppendLog("[parse-error] " + ex.Message + " | " + line + "\n");
                }
            }
            else
            {
                AppendLog(line + "\n");
            }
        }

        private void CancelMigration()
        {
            if (_engineProcess != null && !_engineProcess.HasExited)
            {
                try { _engineProcess.Kill(true); } catch { }
                AppendLog("\n[تم إلغاء العملية من المستخدم]\n");
                SetStatus("أُلغيت العملية.", Theme.NeonAmber);
            }
        }

        private void AppendLog(string text)
        {
            if (_logBox.InvokeRequired)
            {
                _logBox.BeginInvoke(new Action(() => AppendLog(text)));
                return;
            }
            _logBox.AppendText(text);
            _logBox.SelectionStart = _logBox.TextLength;
            _logBox.ScrollToCaret();
        }

        private void SetStatus(string text, Color color)
        {
            if (_statusLabel.InvokeRequired)
            {
                _statusLabel.BeginInvoke(new Action(() => SetStatus(text, color)));
                return;
            }
            _statusLabel.Text = text;
            _statusLabel.ForeColor = color;
        }
    }
}
