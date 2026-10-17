using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Collections.Generic;
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
        private bool _reportedSuccess;
        private bool _reportedCandidate;

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
                Text = "⚡ WhatsApp Migration Engine — Experimental Candidate",
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
                Text = "مرشح تجريبي للنقل ثنائي الاتجاه — لا يوجد نقل معتمد من الجهاز",
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
                "من أندرويد إلى آيفون (Android → iOS)",
                "من آيفون إلى أندرويد (iOS → Android)"
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
            _hexKeyBox.UseSystemPasswordChar = true;
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

            AddPathRow(pathsPanel, "قاعدة بيانات أندرويد:", _androidDbBox, 20, false, "اختر msgstore.db أو crypt15...");
            AddPathRow(pathsPanel, "مجلد ميديا أندرويد:", _androidMediaBox, 62, true, "اختر مجلد Media...");
            AddPathRow(pathsPanel, "نسخة iOS الاحتياطية:", _iosBackupBox, 104, true, "اختر مجلد Backup...");
            AddPathRow(pathsPanel, "مسار حفظ الإخراج (msgstore.db):", _outputDbBox, 146, false, "اختر مكان حفظ msgstore.db...", true);
            AddPathRow(pathsPanel, "ملف crypt15 مرجعي (اختياري):", _referenceCryptBox, 188, false, "اختر crypt15 مرجعي...");

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

        private void AddPathRow(NeonPanel parent, string labelText, TextBox box,
                                 int top, bool isFolder, string placeholder, bool isSaveFile = false)
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
                else if (isSaveFile)
                {
                    using var dlg = new SaveFileDialog { Filter = "WhatsApp database (*.db)|*.db|All files (*.*)|*.*" };
                    if (dlg.ShowDialog() == DialogResult.OK) box.Text = dlg.FileName;
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

        private string LocateEngine(out string? scriptPath)
        {
            scriptPath = null;
            // 1) بجانب EXE (بعد التغليف بـ PyInstaller)
            var exePath = Path.Combine(AppContext.BaseDirectory, "main_engine.exe");
            if (File.Exists(exePath)) return exePath;

            // 2) مسار تطوير محلي
            var devPath = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..",
                                        "main_engine.py");
            if (File.Exists(devPath)) { scriptPath = Path.GetFullPath(devPath); return "python"; }

            throw new FileNotFoundException(
                "لم يتم العثور على main_engine.exe بجانب التطبيق ولا main_engine.py في مسار التطوير.");
        }

        private async Task StartMigrationAsync()
        {
            if (_isRunning) return;

            string hexKey = _hexKeyBox.Text.Trim();
            bool requiresKey = _androidDbBox.Text.Trim().EndsWith(".crypt15", StringComparison.OrdinalIgnoreCase);
            if ((hexKey.Length > 0 && (hexKey.Length != 64 || !IsHex(hexKey))) || (requiresKey && hexKey.Length == 0))
            {
                MessageBox.Show("المفتاح يجب أن يكون 64 حرف hex.", "خطأ في المفتاح",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            try
            {
                string engine = LocateEngine(out string? scriptPath);
                IReadOnlyList<string> args = BuildArguments();
                _logBox.Clear();
                AppendLog("> بدء تشغيل مرشح النقل (تم إخفاء المفتاح والمعاملات الحساسة).\n");
                _reportedSuccess = false;
                _reportedCandidate = false;
                _isRunning = true;
                _startBtn.Enabled = false;
                _cancelBtn.Enabled = true;
                _progress.Value = 0;
                SetStatus("جارٍ تشغيل المرشح التجريبي...", Theme.NeonAmber);
                await RunEngineAsync(engine, scriptPath, args, hexKey);
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

        private IReadOnlyList<string> BuildArguments()
        {
            var args = new List<string>();
            bool androidToIos = _directionCombo.SelectedIndex == 0;
            args.Add("--direction"); args.Add(androidToIos ? "android_to_ios" : "ios_to_android");
            args.Add("--whatsapp-type"); args.Add(_waTypeCombo.SelectedIndex == 1 ? "business" : "regular");
            if (androidToIos)
            {
                args.Add("--android-db"); args.Add(_androidDbBox.Text.Trim());
                args.Add("--android-media"); args.Add(_androidMediaBox.Text.Trim());
                args.Add("--ios-backup"); args.Add(_iosBackupBox.Text.Trim());
            }
            else
            {
                args.Add("--ios-backup"); args.Add(_iosBackupBox.Text.Trim());
                args.Add("--output-db"); args.Add(_outputDbBox.Text.Trim());
                if (!string.IsNullOrWhiteSpace(_referenceCryptBox.Text))
                { args.Add("--reference-crypt"); args.Add(_referenceCryptBox.Text.Trim()); }
            }
            return args;
        }

        private static bool IsHex(string value)
        {
            foreach (char c in value) if (!Uri.IsHexDigit(c)) return false;
            return true;
        }

        private async Task RunEngineAsync(string executable, string? scriptPath,
                                          IReadOnlyList<string> arguments, string hexKey)
        {
            var psi = new ProcessStartInfo
            {
                FileName = executable,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            if (scriptPath != null) psi.ArgumentList.Add(scriptPath);
            foreach (string argument in arguments) psi.ArgumentList.Add(argument);
            psi.Environment["WHATSAPP_MIGRATION_HEX_KEY"] = hexKey;

            using var process = new Process { StartInfo = psi };
            _engineProcess = process;
            string Redact(string value) => hexKey.Length == 0 ? value : value.Replace(hexKey, "[REDACTED]");
            process.OutputDataReceived += (_, e) => { if (e.Data != null) HandleOutputLine(Redact(e.Data)); };
            process.ErrorDataReceived += (_, e) => { if (e.Data != null) AppendLog("[STDERR] " + Redact(e.Data) + "\n"); };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            await process.WaitForExitAsync();
            int exitCode = process.ExitCode;
            AppendLog($"\n[محرك بايثون انتهى بكود {exitCode}]\n");
            if (exitCode == 0 && _reportedSuccess)
            {
                SetStatus("اكتملت العملية بنجاح ✔", Theme.NeonGreen);
                _progress.Value = 100;
            }
            else
            {
                SetStatus(exitCode == 0 ? "انتهى المرشح دون إثبات نقل معتمد." : $"فشل المحرك (كود {exitCode})",
                    exitCode == 0 ? Theme.NeonAmber : Theme.NeonRed);
            }
        }

        private void HandleOutputLine(string line)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => HandleOutputLine(line)));
                return;
            }
            const string prefix = "::PROGRESS::";
            if (line.StartsWith(prefix))
            {
                try
                {
                    using var doc = JsonDocument.Parse(line.Substring(prefix.Length));
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
                        case "candidate":
                        case "candidate_success":
                            _reportedCandidate = true;
                            AppendLog($"⚠ مرشح تجريبي: {msg}\n");
                            if (root.TryGetProperty("output_db", out var candidatePath))
                                AppendLog("قاعدة الفحص: " + candidatePath.GetString() + "\n");
                            if (root.TryGetProperty("report", out var reportPath))
                                AppendLog("تقرير القيود والأعداد: " + reportPath.GetString() + "\n");
                            SetStatus("مرشح تجريبي — لم يُثبت النقل ولم تُعدّل النسخة الاحتياطية.", Theme.NeonAmber);
                            break;
                        case "success":
                            if (_reportedCandidate)
                            {
                                AppendLog($"⚠ تم تجاهل نجاح غير معتمد بعد حدث المرشح: {msg}\n");
                                SetStatus("مرشح تجريبي — لم يُثبت النقل ولم تُعدّل النسخة الاحتياطية.", Theme.NeonAmber);
                                break;
                            }
                            _reportedSuccess = true;
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
