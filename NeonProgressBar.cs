using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace WhatsAppMigrator
{
    public class NeonProgressBar : Control
    {
        private int _value;
        public int Value
        {
            get => _value;
            set { _value = Math.Max(0, Math.Min(100, value)); Invalidate(); }
        }
        public Color BarColor { get; set; } = Theme.NeonGreen;

        public NeonProgressBar()
        {
            DoubleBuffered = true;
            Height = 12;
            BackColor = Theme.SurfaceAlt;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            var bgRect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var path = Rounded(bgRect, Height / 2))
            {
                using var bg = new SolidBrush(Theme.SurfaceAlt);
                g.FillPath(bg, path);
                using var border = new Pen(Theme.Border, 1);
                g.DrawPath(border, path);
            }

            int fillWidth = (int)((Width - 2) * (_value / 100.0));
            if (fillWidth > 1)
            {
                var fgRect = new Rectangle(1, 1, fillWidth, Height - 2);
                using var path = Rounded(fgRect, (Height - 2) / 2);
                using var gradient = new LinearGradientBrush(
                    fgRect,
                    Color.FromArgb(200, BarColor),
                    BarColor,
                    LinearGradientMode.Horizontal);
                g.FillPath(gradient, path);
            }
        }

        private static GraphicsPath Rounded(Rectangle r, int rad)
        {
            var p = new GraphicsPath();
            int d = Math.Max(2, rad * 2);
            if (d > r.Height) d = r.Height;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }
}
