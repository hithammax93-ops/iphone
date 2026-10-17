using System.Drawing;

namespace WhatsAppMigrator
{
    internal static class Theme
    {
        // خلفيات
        public static readonly Color Background   = Color.FromArgb(13, 13, 18);
        public static readonly Color Surface      = Color.FromArgb(22, 22, 30);
        public static readonly Color SurfaceAlt   = Color.FromArgb(30, 30, 42);
        public static readonly Color Border       = Color.FromArgb(50, 50, 70);

        // ألوان النيون
        public static readonly Color NeonGreen    = Color.FromArgb(57, 255, 20);
        public static readonly Color NeonCyan     = Color.FromArgb(0, 240, 255);
        public static readonly Color NeonPurple   = Color.FromArgb(180, 80, 255);
        public static readonly Color NeonPink     = Color.FromArgb(255, 60, 180);
        public static readonly Color NeonAmber    = Color.FromArgb(255, 180, 60);
        public static readonly Color NeonRed      = Color.FromArgb(255, 70, 90);

        // نصوص
        public static readonly Color TextPrimary  = Color.FromArgb(235, 235, 245);
        public static readonly Color TextSecondary= Color.FromArgb(150, 150, 170);

        // خطوط
        public static readonly Font HeaderFont = new Font("Segoe UI", 18f, FontStyle.Bold);
        public static readonly Font LabelFont  = new Font("Segoe UI", 9.75f, FontStyle.Regular);
        public static readonly Font ButtonFont = new Font("Segoe UI Semibold", 10f, FontStyle.Bold);
        public static readonly Font MonoFont   = new Font("Consolas", 9f, FontStyle.Regular);
    }
}
