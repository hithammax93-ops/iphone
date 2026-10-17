using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace WhatsAppMigrator
{
    public class NeonButton : Button
    {
        public Color NeonColor { get; set; } = Theme.NeonGreen;
        public int CornerRadius { get; set; } = 8;

        private bool _hover;

        public NeonButton()
        {
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            BackColor = Theme.SurfaceAlt;
            ForeColor = Theme.TextPrimary;
            Font = Theme.ButtonFont;
            Cursor = Cursors.Hand;
            Height = 42;
            SetStyle(ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.UserPaint |
                     ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw, true);
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var path = RoundedPath(rect, CornerRadius))
            {
                // توهج
                if (_hover)
                {
                    using var glow = new Pen(Color.FromArgb(140, NeonColor), 3);
                    g.DrawPath(glow, path);
                }
                using (var fill = new SolidBrush(_hover ? Color.FromArgb(40, NeonColor) : BackColor))
                    g.FillPath(fill, path);

                using var border = new Pen(NeonColor, _hover ? 2 : 1);
                g.DrawPath(border, path);
            }

            TextRenderer.DrawText(g, Text, Font, ClientRectangle, NeonColor,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }

        private static GraphicsPath RoundedPath(Rectangle r, int radius)
        {
            var p = new GraphicsPath();
            int d = radius * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }
}
