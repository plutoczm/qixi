// Compact, native warm-toned startup feedback for the personal desktop EXE.
using System;
using System.Drawing;
using System.Windows.Forms;

namespace XunqiLauncher
{
    internal sealed class StartupWindow : Form
    {
        private readonly Label status;
        private readonly ProgressBar progress;

        internal StartupWindow(Icon icon, Action cancel)
        {
            Text = "寻栖 · 正在开启";
            Icon = icon;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(440, 224);
            MinimumSize = MaximumSize = Size;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(255, 249, 240);
            Font = new Font("Microsoft YaHei UI", 10, FontStyle.Regular);
            ShowInTaskbar = true;

            Panel accent = new Panel();
            accent.Location = new Point(0, 0);
            accent.Size = new Size(440, 8);
            accent.BackColor = Color.FromArgb(171, 119, 84);
            Controls.Add(accent);

            Label mark = new Label();
            mark.Text = "栖";
            mark.TextAlign = ContentAlignment.MiddleCenter;
            mark.Location = new Point(25, 39);
            mark.Size = new Size(63, 63);
            mark.Font = new Font("Microsoft YaHei UI", 27, FontStyle.Bold);
            mark.ForeColor = Color.White;
            mark.BackColor = Color.FromArgb(171, 119, 84);
            Controls.Add(mark);

            Label title = new Label();
            title.Text = "寻栖";
            title.Location = new Point(110, 40);
            title.Size = new Size(294, 38);
            title.Font = new Font("Microsoft YaHei UI", 21, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(92, 60, 40);
            Controls.Add(title);

            Label subtitle = new Label();
            subtitle.Text = "让每一次入住，都更从容";
            subtitle.Location = new Point(111, 82);
            subtitle.Size = new Size(300, 25);
            subtitle.Font = new Font("Microsoft YaHei UI", 9, FontStyle.Regular);
            subtitle.ForeColor = Color.FromArgb(135, 107, 84);
            Controls.Add(subtitle);

            status = new Label();
            status.Text = "正在准备本地服务…";
            status.Location = new Point(27, 131);
            status.Size = new Size(380, 23);
            status.ForeColor = Color.FromArgb(105, 77, 58);
            status.Font = new Font("Microsoft YaHei UI", 9, FontStyle.Regular);
            Controls.Add(status);

            progress = new ProgressBar();
            progress.Style = ProgressBarStyle.Marquee;
            progress.MarqueeAnimationSpeed = 28;
            progress.Location = new Point(27, 164);
            progress.Size = new Size(303, 8);
            Controls.Add(progress);

            Button cancelButton = new Button();
            cancelButton.Text = "取消启动";
            cancelButton.FlatStyle = FlatStyle.Flat;
            cancelButton.FlatAppearance.BorderColor = Color.FromArgb(222, 200, 180);
            cancelButton.BackColor = Color.FromArgb(250, 237, 222);
            cancelButton.ForeColor = Color.FromArgb(110, 70, 47);
            cancelButton.Location = new Point(340, 153);
            cancelButton.Size = new Size(88, 34);
            cancelButton.Click += delegate { cancel(); };
            Controls.Add(cancelButton);
        }

        internal void SetStatus(string message)
        {
            status.Text = message;
            status.Refresh();
        }
    }
}
