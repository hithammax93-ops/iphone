using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace WhatsAppMigrator
{
    public class MainForm : Form
    {
        private readonly ComboBox _directionCombo = new();
        private readonly ComboBox _waTypeCombo = new();
        private readonly ComboBox _serialCombo = new();
        private readonly TextBox _backupDirBox = new();
        private readonly TextBox _hexKeyBox = new();
        private readonly TextBox _androidDbBox = new();
        private readonly TextBox _androidMediaBox = new();
        private readonly TextBox _iosBackupBox = new();
        private readonly TextBox _outputDbBox = new();
        private readonly TextBox _referenceCryptBox = new();
        private readonly NeonProgressBar _progress = new();
        private readonly RichTextBox _logBox = new();
        private readonly NeonButton _checkBtn = new();
        private readonly NeonButton _backupBtn = new();
        private readonly NeonButton _candidateBtn = new();
        private readonly NeonButton _cancelBtn = new();
        private readonly Label _statusLabel = new();
        private readonly Label _deviceStateLabel = new();
        private readonly Label _backupStateLabel = new();

        private Process? _engineProcess;
        private bool _isRunning;
        private bool _reportedSuccess;
        private bool _reportedCandidate;
        private bool _terminalEventSeen;
        private bool _backupAttempted;
        private bool _backupReady;
        private int _pendingUiCallbacks;
        private TaskCompletionSource<bool>? _uiDrain;

        private sealed class DeviceItem
        {
            public string Serial { get; init; } = "";
            public string State { get; init; } = "";
            public string Model { get; init; } = "";
            public override string ToString() => string.IsNullOrWhiteSpace(Model) ? $"{Serial} — {State}" : $"{Model} ({Serial}) — {State}";
        }

        public MainForm()
        {
            Text = "WhatsApp Migrator — أدوات أندرويد ومرشح تجريبي";
            Width = 1180; Height = 820; MinimumSize = new Size(980, 700);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Theme.Background; ForeColor = Theme.TextPrimary; Font = Theme.LabelFont;
            BuildUI();
        }

        private void BuildUI()
        {
            var root = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 6, Padding = new Padding(10), BackColor = Theme.Background };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 116));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 164));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 150));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Controls.Add(root);

            var header = new Label { Text = "WhatsApp Migration Engine — أدوات أندرويد", Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(12, 0, 0, 0), Font = new Font("Segoe UI", 19, FontStyle.Bold), ForeColor = Theme.NeonGreen, BackColor = Theme.Surface };
            root.Controls.Add(header, 0, 0);
            var subtitle = new Label { Text = "الفحص والنسخ من هاتف أندرويد فقط. الناتج التحويلي مرشح تجريبي وليس نقلًا إلى آيفون.", Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(14, 0, 0, 0), ForeColor = Theme.TextSecondary, BackColor = Theme.Surface };
            root.Controls.Add(subtitle, 0, 1);

            var general = Panel("الإعدادات العامة", Theme.NeonCyan);
            var grid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 6, RowCount = 2, Padding = new Padding(12, 8, 12, 8) };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 38));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 24));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 38));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 38)); grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            AddLabel(grid, "العملية:", 0, 0, Theme.NeonCyan); ConfigureCombo(_directionCombo, new[] { "1 فحص الهاتف (أندرويد فقط)", "2 نسخ أندرويد إلى اللابتوب", "3 إنشاء مرشح تحويل (ليس نقلًا إلى آيفون)" }); grid.Controls.Add(_directionCombo, 1, 0); _directionCombo.SelectedIndexChanged += (_, _) => UpdateVisibility();
            AddLabel(grid, "واتساب:", 2, 0, Theme.NeonPurple); ConfigureCombo(_waTypeCombo, new[] { "عادي (regular)", "أعمال (business)" }); grid.Controls.Add(_waTypeCombo, 3, 0);
            AddLabel(grid, "جهاز:", 4, 0, Theme.NeonAmber); ConfigureCombo(_serialCombo, Array.Empty<string>()); grid.Controls.Add(_serialCombo, 5, 0);
            _deviceStateLabel.Text = "لم يُجرَ فحص بعد."; _deviceStateLabel.ForeColor = Theme.TextSecondary; _deviceStateLabel.Dock = DockStyle.Fill; grid.Controls.Add(_deviceStateLabel, 1, 1); grid.SetColumnSpan(_deviceStateLabel, 2);
            AddLabel(grid, "حالة النسخ:", 4, 1, Theme.NeonAmber); _backupStateLabel.Text = "لا توجد نسخة جاهزة."; _backupStateLabel.ForeColor = Theme.TextSecondary; _backupStateLabel.Dock = DockStyle.Fill; grid.Controls.Add(_backupStateLabel, 5, 1);
            general.Controls.Add(grid); root.Controls.Add(general, 0, 2);

            var paths = Panel("مسارات النسخ والمرشح", Theme.NeonPink);
            var pgrid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 4, Padding = new Padding(12, 6, 12, 6) };
            pgrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 220)); pgrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); pgrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 50)); pgrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 190));
            for (int i = 0; i < 4; i++) pgrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
            AddLabel(pgrid, "مجلد حفظ النسخ على اللابتوب:", 0, 0, Theme.NeonCyan); AddText(pgrid, _backupDirBox, 1, 0); pgrid.Controls.Add(BrowseButton(_backupDirBox, true), 2, 0); pgrid.Controls.Add(new Label { Text = "ملفات مشتركة + تقرير manifest", Dock = DockStyle.Fill, ForeColor = Theme.TextSecondary, TextAlign = ContentAlignment.MiddleLeft }, 3, 0);
            AddLabel(pgrid, "قاعدة أندرويد / crypt15:", 0, 1, Theme.TextSecondary); AddText(pgrid, _androidDbBox, 1, 1); pgrid.Controls.Add(BrowseButton(_androidDbBox, false), 2, 1);
            AddLabel(pgrid, "مجلد Media أندرويد:", 0, 2, Theme.TextSecondary); AddText(pgrid, _androidMediaBox, 1, 2); pgrid.Controls.Add(BrowseButton(_androidMediaBox, true), 2, 2);
            AddLabel(pgrid, "نسخة iOS (مطلوبة للمرشح فقط):", 0, 3, Theme.TextSecondary); AddText(pgrid, _iosBackupBox, 1, 3); pgrid.Controls.Add(BrowseButton(_iosBackupBox, true), 2, 3);
            var keyLabel = new Label { Text = "مفتاح crypt15 (لا يُطبع):", Dock = DockStyle.Fill, ForeColor = Theme.NeonAmber, TextAlign = ContentAlignment.MiddleLeft }; pgrid.Controls.Add(keyLabel, 3, 1); _hexKeyBox.Dock = DockStyle.Fill; _hexKeyBox.UseSystemPasswordChar = true; _hexKeyBox.BackColor = Theme.SurfaceAlt; _hexKeyBox.ForeColor = Theme.NeonAmber; _hexKeyBox.Font = Theme.MonoFont; pgrid.Controls.Add(_hexKeyBox, 3, 2); paths.Controls.Add(pgrid); root.Controls.Add(paths, 0, 3);

            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = true, AutoScroll = true, Padding = new Padding(8, 8, 8, 4), BackColor = Theme.Background };
            ConfigureButton(_checkBtn, "1  فحص الهاتف", Theme.NeonCyan); _checkBtn.Click += async (_, _) => await RunOperationAsync("check_devices"); actions.Controls.Add(_checkBtn);
            ConfigureButton(_backupBtn, "2  نسخ أندرويد إلى اللابتوب", Theme.NeonGreen); _backupBtn.Click += async (_, _) => await RunOperationAsync("backup_android"); actions.Controls.Add(_backupBtn);
            ConfigureButton(_candidateBtn, "3  إنشاء مرشح تحويل", Theme.NeonPurple); _candidateBtn.Click += async (_, _) => await RunOperationAsync("android_to_ios"); actions.Controls.Add(_candidateBtn);
            ConfigureButton(_cancelBtn, "إلغاء", Theme.NeonRed); _cancelBtn.Enabled = false; _cancelBtn.Click += (_, _) => CancelMigration(); actions.Controls.Add(_cancelBtn);
            _statusLabel.Text = "جاهز."; _statusLabel.AutoSize = false; _statusLabel.Width = 360; _statusLabel.Height = 38; _statusLabel.TextAlign = ContentAlignment.MiddleLeft; _statusLabel.ForeColor = Theme.NeonCyan; _progress.Width = 150; _progress.Height = 20; _progress.BarColor = Theme.NeonGreen; actions.Controls.Add(_progress); actions.Controls.Add(_statusLabel); root.Controls.Add(actions, 0, 4);

            var logPanel = Panel("سجل المحرك", Theme.NeonCyan); _logBox.Dock = DockStyle.Fill; _logBox.BackColor = Color.Black; _logBox.ForeColor = Theme.NeonGreen; _logBox.Font = Theme.MonoFont; _logBox.ReadOnly = true; _logBox.BorderStyle = BorderStyle.None; _logBox.WordWrap = false; _logBox.ScrollBars = RichTextBoxScrollBars.Both; logPanel.Controls.Add(_logBox); root.Controls.Add(logPanel, 0, 5);
            _directionCombo.SelectedIndex = 0; _waTypeCombo.SelectedIndex = 0; UpdateVisibility();
        }

        private NeonPanel Panel(string title, Color color) { var p = new NeonPanel { Dock = DockStyle.Fill, BorderColor = color, Padding = new Padding(4) }; p.Controls.Add(new Label { Text = title, Dock = DockStyle.Top, Height = 22, ForeColor = color, Font = new Font("Segoe UI", 9, FontStyle.Bold) }); return p; }
        private static void AddLabel(TableLayoutPanel g, string text, int c, int r, Color color) { g.Controls.Add(new Label { Text = text, Dock = DockStyle.Fill, ForeColor = color, TextAlign = ContentAlignment.MiddleLeft }, c, r); }
        private void AddText(TableLayoutPanel g, TextBox b, int c, int r) { b.Dock = DockStyle.Fill; b.BackColor = Theme.SurfaceAlt; b.ForeColor = Theme.TextPrimary; b.BorderStyle = BorderStyle.FixedSingle; b.Font = Theme.MonoFont; g.Controls.Add(b, c, r); }
        private static void ConfigureCombo(ComboBox c, IEnumerable<string> values) { c.Dock = DockStyle.Fill; c.DropDownStyle = ComboBoxStyle.DropDownList; c.BackColor = Theme.SurfaceAlt; c.ForeColor = Theme.TextPrimary; c.FlatStyle = FlatStyle.Flat; c.Items.AddRange(new List<string>(values).ToArray()); }
        private static void ConfigureButton(NeonButton b, string text, Color color) { b.Text = text; b.NeonColor = color; b.Width = 205; b.Height = 38; b.Margin = new Padding(4); }
        private NeonButton BrowseButton(TextBox box, bool folder) { var b = new NeonButton { Text = "…", NeonColor = Theme.NeonCyan, Width = 42, Height = 28, Margin = new Padding(2) }; b.Click += (_, _) => { if (folder) { using var d = new FolderBrowserDialog(); if (d.ShowDialog() == DialogResult.OK) box.Text = d.SelectedPath; } else { using var d = new OpenFileDialog(); if (d.ShowDialog() == DialogResult.OK) box.Text = d.FileName; } }; return b; }

        private void UpdateVisibility()
        {
            bool check = _directionCombo.SelectedIndex == 0, backup = _directionCombo.SelectedIndex == 1;
            _serialCombo.Enabled = !check; _backupDirBox.Enabled = backup; _androidDbBox.Enabled = !check; _androidMediaBox.Enabled = !check; _iosBackupBox.Enabled = !check; _hexKeyBox.Enabled = !check;
        }

        private string LocateEngine(out string? scriptPath)
        {
            scriptPath = null;
            var exe = Path.Combine(AppContext.BaseDirectory, "main_engine.exe"); if (File.Exists(exe)) return exe;
            var bundledPython = Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe");
            var bundledScript = Path.Combine(AppContext.BaseDirectory, "main_engine.py");
            if (File.Exists(bundledPython) && File.Exists(bundledScript)) { scriptPath = bundledScript; return bundledPython; }
            var candidates = new[] { bundledScript, Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "main_engine.py")), Path.Combine(Directory.GetCurrentDirectory(), "main_engine.py") };
            foreach (var path in candidates) if (File.Exists(path)) { scriptPath = path; return "python"; }
            throw new FileNotFoundException("لم يتم العثور على main_engine.exe أو main_engine.py.");
        }

        private async Task RunOperationAsync(string direction)
        {
            if (_isRunning) return;
            if (direction == "backup_android" && string.IsNullOrWhiteSpace(_backupDirBox.Text)) { MessageBox.Show("اختر مجلد حفظ النسخة أولًا.", "مجلد النسخ", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            if (direction == "backup_android" && _serialCombo.SelectedItem is not DeviceItem) { MessageBox.Show("نفّذ فحص الهاتف واختر جهازًا أولًا.", "الجهاز", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            if (direction == "backup_android" && _serialCombo.SelectedItem is DeviceItem selectedDevice && !selectedDevice.State.Equals("device", StringComparison.OrdinalIgnoreCase)) { MessageBox.Show("النسخ يتطلب جهاز أندرويد بحالة device (وليس authorized).", "الجهاز غير جاهز", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            if (direction == "android_to_ios" && !ValidateKey()) return;
            try
            {
                if (direction == "backup_android") { _backupAttempted = true; _backupReady = false; _androidDbBox.Clear(); _androidMediaBox.Clear(); _backupStateLabel.Text = "جارٍ بدء النسخ؛ لا توجد نسخة جاهزة."; }
                string engine = LocateEngine(out string? script); var args = BuildArguments(direction); _logBox.Clear(); _reportedSuccess = false; _reportedCandidate = false; _terminalEventSeen = false; _isRunning = true; SetButtons(false); _progress.Value = 0; SetStatus(direction == "check_devices" ? "جارٍ فحص أجهزة أندرويد…" : "جارٍ تشغيل العملية…", Theme.NeonAmber); await RunEngineAsync(engine, script, args, direction == "android_to_ios" ? _hexKeyBox.Text.Trim() : "");
            }
            catch (Exception ex) { AppendLog("[EXCEPTION] " + ex.Message + "\n"); SetStatus("فشل: " + ex.Message, Theme.NeonRed); }
            finally { _isRunning = false; SetButtons(true); _engineProcess = null; }
        }

        private bool ValidateKey() { string key = _hexKeyBox.Text.Trim(); bool crypt = _androidDbBox.Text.Trim().EndsWith(".crypt15", StringComparison.OrdinalIgnoreCase); if (key.Length > 0 && (key.Length != 64 || !IsHex(key))) { MessageBox.Show("مفتاح crypt15 يجب أن يكون 64 حرف hex.", "خطأ في المفتاح", MessageBoxButtons.OK, MessageBoxIcon.Warning); return false; } if (crypt && key.Length == 0) { MessageBox.Show("ملف crypt15 مشفّر. أدخل مفتاحه الصحيح لفكّه (أو استخدم wadecrypt خارجيًا ثم أدخل قاعدة مفكوكة). الناتج يظل مرشحًا تجريبيًا ولا يعني الجاهزية.", "مفتاح crypt15 مطلوب", MessageBoxButtons.OK, MessageBoxIcon.Warning); return false; } return true; }
        private void SetButtons(bool enabled) { _checkBtn.Enabled = enabled; _backupBtn.Enabled = enabled; _candidateBtn.Enabled = enabled; _cancelBtn.Enabled = !enabled; }

        private IReadOnlyList<string> BuildArguments(string direction)
        {
            var a = new List<string> { "--direction", direction, "--whatsapp-type", _waTypeCombo.SelectedIndex == 1 ? "business" : "regular" };
            if (direction == "backup_android") { a.AddRange(new[] { "--android-serial", ((DeviceItem)_serialCombo.SelectedItem!).Serial, "--backup-dir", _backupDirBox.Text.Trim() }); }
            else if (direction == "android_to_ios") { a.AddRange(new[] { "--android-db", _androidDbBox.Text.Trim(), "--android-media", _androidMediaBox.Text.Trim(), "--ios-backup", _iosBackupBox.Text.Trim() }); }
            return a;
        }
        private static bool IsHex(string value) { foreach (char c in value) if (!Uri.IsHexDigit(c)) return false; return true; }

        private async Task RunEngineAsync(string executable, string? scriptPath, IReadOnlyList<string> arguments, string hexKey)
        {
            var psi = new ProcessStartInfo { FileName = executable, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            if (scriptPath != null) psi.ArgumentList.Add(scriptPath); foreach (var arg in arguments) psi.ArgumentList.Add(arg); psi.Environment["WHATSAPP_MIGRATION_HEX_KEY"] = hexKey; psi.Environment["PYTHONIOENCODING"] = "utf-8"; psi.Environment["PYTHONUTF8"] = "1";
            using var process = new Process { StartInfo = psi }; _engineProcess = process;
            string Redact(string s) => hexKey.Length == 0 ? s : s.Replace(hexKey, "[REDACTED]");
            process.OutputDataReceived += (_, e) => { if (e.Data != null) HandleOutputLine(Redact(e.Data)); }; process.ErrorDataReceived += (_, e) => { if (e.Data != null) AppendLog("[STDERR] " + Redact(e.Data) + "\n"); };
            process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine(); await process.WaitForExitAsync(); process.WaitForExit(); await DrainUiAsync();
            int code = process.ExitCode; AppendLog($"\n[انتهى المحرك بكود {code}]\n"); if (code != 0) SetStatus($"فشل المحرك (كود {code})", Theme.NeonRed); else if (!_terminalEventSeen) SetStatus("انتهت العملية دون إعلان نتيجة معتمدة.", Theme.NeonAmber);
        }

        private void HandleOutputLine(string line)
        {
            if (InvokeRequired) { QueueUi(() => HandleOutputLine(line)); return; }
            const string prefix = "::PROGRESS::"; if (!line.StartsWith(prefix)) { AppendLog(line + "\n"); return; }
            try { using var doc = JsonDocument.Parse(line[prefix.Length..]); var root = doc.RootElement; var type = root.TryGetProperty("type", out var t) ? t.GetString() ?? "" : ""; var msg = root.TryGetProperty("message", out var m) ? m.GetString() ?? "" : "";
                switch (type)
                {
                    case "devices": _terminalEventSeen = true; HandleDevices(root, msg); break;
                    case "backup": _terminalEventSeen = true; HandleBackup(root, msg); break;
                    case "log": AppendLog("[" + (root.TryGetProperty("level", out var l) ? l.GetString() : "log") + "] " + msg + "\n"); break;
                    case "step": _progress.Value = Math.Clamp(root.TryGetProperty("percent", out var p) ? p.GetInt32() : 0, 0, 100); SetStatus(msg, Theme.NeonCyan); AppendLog("• " + msg + "\n"); break;
                    case "candidate": case "candidate_success": _terminalEventSeen = true; _reportedCandidate = true; AppendLog("⚠ مرشح تجريبي: " + msg + "\n"); SetStatus("مرشح تجريبي — لم يُثبت النقل ولم تُعدّل نسخة آيفون.", Theme.NeonAmber); break;
                    case "success": _terminalEventSeen = true; _reportedSuccess = true; AppendLog("✔ " + msg + "\n"); SetStatus(msg, Theme.NeonGreen); break;
                    case "error": _terminalEventSeen = true; AppendLog("✖ " + msg + "\n"); SetStatus("خطأ: " + msg, Theme.NeonRed); break;
                }
            } catch (Exception ex) { AppendLog("[parse-error] " + ex.Message + "\n"); }
        }

        private void HandleDevices(JsonElement root, string msg)
        {
            _serialCombo.Items.Clear(); int count = 0; bool adbAvailable = !root.TryGetProperty("adb_available", out var adb) || adb.ValueKind != JsonValueKind.False;
            string adbError = GetString(root, "error");
            if (root.TryGetProperty("devices", out var devices) && devices.ValueKind == JsonValueKind.Array) foreach (var d in devices.EnumerateArray()) { string serial = GetString(d, "serial"); if (string.IsNullOrWhiteSpace(serial)) continue; var item = new DeviceItem { Serial = serial, State = GetString(d, "state"), Model = GetString(d, "model") }; _serialCombo.Items.Add(item); count++; }
            if (count > 0) _serialCombo.SelectedIndex = 0;
            if (!adbAvailable) _deviceStateLabel.Text = "ADB غير متاح" + (string.IsNullOrWhiteSpace(adbError) ? ". ثبّت platform-tools أو أضف adb إلى PATH ثم أعد الفحص." : ": " + adbError + " — ثبّت platform-tools أو أضف adb إلى PATH.");
            else _deviceStateLabel.Text = count == 0 ? "لم يُعثر على أجهزة أندرويد. فعّل USB debugging وافتح الهاتف ثم أعد الفحص." : $"تم العثور على {count} جهاز/أجهزة أندرويد.";
            AppendLog("[devices] " + _deviceStateLabel.Text + "\n"); SetStatus(_deviceStateLabel.Text, count == 0 || !adbAvailable ? Theme.NeonAmber : Theme.NeonCyan);
        }
        private void HandleBackup(JsonElement root, string msg)
        {
            _backupAttempted = true; string status = GetString(root, "status"); bool statusSuccess = status.Equals("success", StringComparison.OrdinalIgnoreCase); bool ready = statusSuccess && root.TryGetProperty("conversion_ready", out var r) && r.ValueKind == JsonValueKind.True; _backupReady = ready;
            if (root.TryGetProperty("android_db", out var db) && db.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(db.GetString())) _androidDbBox.Text = db.GetString()!; if (root.TryGetProperty("android_media", out var media) && media.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(media.GetString())) _androidMediaBox.Text = media.GetString()!;
            string dir = GetString(root, "backup_dir"); string manifest = GetString(root, "manifest"); string reasons = GetString(root, "conversion_reasons"); string errors = root.TryGetProperty("errors", out var errorValue) ? errorValue.GetRawText() : ""; string detail = string.IsNullOrWhiteSpace(msg) ? status : msg; if (!string.IsNullOrWhiteSpace(manifest)) detail += " — manifest: " + manifest; if (!string.IsNullOrWhiteSpace(reasons)) detail += " — conversion_reasons: " + reasons; if (!string.IsNullOrWhiteSpace(errors) && errors != "[]") detail += " — errors: " + errors;
            AppendLog("[backup] " + detail + (string.IsNullOrWhiteSpace(dir) ? "" : " — " + dir) + "\n"); _backupStateLabel.Text = ready ? "الملفات المشتركة جاهزة لمرشح تجريبي فقط." : "النسخ غير جاهز للتحويل."; SetStatus(_backupStateLabel.Text, ready ? Theme.NeonGreen : Theme.NeonAmber);
            bool encrypted = status.Contains("encrypt", StringComparison.OrdinalIgnoreCase) || msg.Contains("encrypt", StringComparison.OrdinalIgnoreCase) || status.Contains("مشفر") || msg.Contains("مشفر") || reasons.Contains("encrypt", StringComparison.OrdinalIgnoreCase) || reasons.Contains("مشفر"); if (encrypted) MessageBox.Show("تم العثور على نسخة مشفّرة/crypt15. أدخل المفتاح الصحيح أو فكّها خارجيًا باستخدام wadecrypt ثم استخدم الملف الناتج. قد يظل الناتج مرشحًا تجريبيًا ولا تعني هذه الرسالة الجاهزية.", "نسخ مشفّر", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        private static string GetString(JsonElement e, string name) => e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

        private void CancelMigration() { try { if (_engineProcess != null && !_engineProcess.HasExited) _engineProcess.Kill(true); } catch { } AppendLog("\n[تم إلغاء العملية من المستخدم]\n"); SetStatus("أُلغيت العملية.", Theme.NeonAmber); }
        private void QueueUi(Action action) { lock (this) _pendingUiCallbacks++; try { BeginInvoke(new Action(() => { try { action(); } finally { lock (this) { if (--_pendingUiCallbacks == 0) _uiDrain?.TrySetResult(true); } } })); } catch { lock (this) { _pendingUiCallbacks--; } } }
        private async Task DrainUiAsync() { Task? wait = null; lock (this) { if (_pendingUiCallbacks > 0) { _uiDrain = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously); wait = _uiDrain.Task; } } if (wait != null) await wait; }
        private void AppendLog(string text) { if (_logBox.InvokeRequired) { QueueUi(() => AppendLog(text)); return; } _logBox.AppendText(text); _logBox.SelectionStart = _logBox.TextLength; _logBox.ScrollToCaret(); }
        private void SetStatus(string text, Color color) { if (_statusLabel.InvokeRequired) { QueueUi(() => SetStatus(text, color)); return; } _statusLabel.Text = text; _statusLabel.ForeColor = color; }
    }
}
