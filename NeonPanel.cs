using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace WhatsAppMigrator
{
    public class NeonPanel : Panel
    {
        public Color BorderColor { get; set; } = Theme.NeonCyan;
        public int CornerRadius { get; set; } = 10;

        public NeonPanel()
        {
            DoubleBuffered = true;
            BackColor = Theme.Surface;
            Padding = new Padding(12);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new Rectangle(0, 0, Width - 1, Height - 1);
            using var path = Rounded(r, CornerRadius);
            using var fill = new SolidBrush(BackColor);
            g.FillPath(fill, path);
            using var pen = new Pen(BorderColor, 1.2f);
            g.DrawPath(pen, path);
            base.OnPaint(e);
        }

        private static GraphicsPath Rounded(Rectangle rect, int rad)
        {
            var p = new GraphicsPath();
            int d = rad * 2;
            p.AddArc(rect.X, rect.Y, d, d, 180, 90);
            p.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
            p.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
            p.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }
}
