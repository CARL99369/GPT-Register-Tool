namespace SmsWorkbench
{
    public partial class MainWindow
    {
        private bool ShowOneClickSmsSourceDialog(string configuredSource, out bool useApiPool)
        {
            useApiPool = false;
            string configuredLabel = configuredSource == "sms66"
                ? "SMS66（当前配置）"
                : "SMSBower（当前配置）";
            var dialog = new Window
            {
                Title = "一键接码",
                Owner = this,
                Width = Math.Min(520, SystemParameters.WorkArea.Width - 60),
                Height = 300,
                MinWidth = 460,
                MinHeight = 280,
                ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = (Brush)FindResource("AppBg")
            };

            var root = new Grid { Margin = new Thickness(24) };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            var heading = new TextBlock
            {
                Text = "选择号码来源",
                FontSize = 20,
                FontWeight = FontWeights.SemiBold,
                Foreground = (Brush)FindResource("TextMain"),
                Margin = new Thickness(0, 0, 0, 8)
            };
            Grid.SetRow(heading, 0);
            root.Children.Add(heading);

            var description = new TextBlock
            {
                Text = "API 接码池仅用于本次任务，不会修改已保存的供应商配置。",
                FontSize = 13,
                Foreground = (Brush)FindResource("TextSub"),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 18)
            };
            Grid.SetRow(description, 1);
            root.Children.Add(description);

            var sourceBox = new ComboBox
            {
                ItemsSource = new[] { configuredLabel, "API 接码池（号码---URL）" },
                SelectedIndex = 0,
                MinHeight = 38,
                Padding = new Thickness(10, 5, 10, 5)
            };
            Grid.SetRow(sourceBox, 2);
            root.Children.Add(sourceBox);

            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 20, 0, 0)
            };
            var cancel = new Button
            {
                Content = "取消",
                MinWidth = 88,
                Height = 36,
                Margin = new Thickness(0, 0, 10, 0),
                IsCancel = true,
                Style = (Style)FindResource("SecondaryButton")
            };
            var next = new Button
            {
                Content = "下一步",
                MinWidth = 96,
                Height = 36,
                IsDefault = true,
                Style = (Style)FindResource("PrimaryButton")
            };
            next.Click += (_, _) => dialog.DialogResult = true;
            buttons.Children.Add(cancel);
            buttons.Children.Add(next);
            Grid.SetRow(buttons, 4);
            root.Children.Add(buttons);

            dialog.Content = root;
            if (dialog.ShowDialog() != true)
                return false;
            useApiPool = sourceBox.SelectedIndex == 1;
            return true;
        }

        private bool ShowApiSmsPoolImportDialog(out IReadOnlyList<ApiSmsPoolEntry> entries)
        {
            entries = Array.Empty<ApiSmsPoolEntry>();
            IReadOnlyList<ApiSmsPoolEntry> accepted = Array.Empty<ApiSmsPoolEntry>();
            var dialog = new Window
            {
                Title = "API 接码池",
                Owner = this,
                Width = Math.Min(720, SystemParameters.WorkArea.Width - 60),
                Height = Math.Min(520, SystemParameters.WorkArea.Height - 60),
                MinWidth = 560,
                MinHeight = 420,
                ResizeMode = ResizeMode.CanResize,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = (Brush)FindResource("AppBg")
            };

            var root = new Grid { Margin = new Thickness(24) };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            var heading = new TextBlock
            {
                Text = "导入 API 接码池",
                FontSize = 20,
                FontWeight = FontWeights.SemiBold,
                Foreground = (Brush)FindResource("TextMain"),
                Margin = new Thickness(0, 0, 0, 6)
            };
            Grid.SetRow(heading, 0);
            root.Children.Add(heading);

            var description = new TextBlock
            {
                Text = "每行一条：号码---URL",
                FontSize = 13,
                Foreground = (Brush)FindResource("TextSub"),
                Margin = new Thickness(0, 0, 0, 14)
            };
            Grid.SetRow(description, 1);
            root.Children.Add(description);

            var input = new TextBox
            {
                AcceptsReturn = true,
                AcceptsTab = false,
                TextWrapping = TextWrapping.NoWrap,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                FontFamily = new FontFamily("Consolas"),
                FontSize = 13,
                Padding = new Thickness(12),
                MinHeight = 220,
                VerticalContentAlignment = VerticalAlignment.Top
            };
            Grid.SetRow(input, 2);
            root.Children.Add(input);

            var status = new TextBlock
            {
                Text = "等待导入",
                FontSize = 12,
                Foreground = (Brush)FindResource("TextMuted"),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 10, 0, 0)
            };
            Grid.SetRow(status, 3);
            root.Children.Add(status);

            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 18, 0, 0)
            };
            var cancel = new Button
            {
                Content = "取消",
                MinWidth = 88,
                Height = 36,
                Margin = new Thickness(0, 0, 10, 0),
                IsCancel = true,
                Style = (Style)FindResource("SecondaryButton")
            };
            var start = new Button
            {
                Content = "开始接码",
                MinWidth = 104,
                Height = 36,
                IsDefault = true,
                IsEnabled = false,
                Style = (Style)FindResource("PrimaryButton")
            };

            void ValidateInput()
            {
                if (ApiSmsPoolImport.TryParse(input.Text, out var parsed, out string error))
                {
                    accepted = parsed;
                    start.IsEnabled = true;
                    status.Text = "已识别 " + parsed.Count + " 个号码";
                    status.Foreground = (Brush)FindResource("Success");
                }
                else
                {
                    accepted = Array.Empty<ApiSmsPoolEntry>();
                    start.IsEnabled = false;
                    status.Text = error;
                    status.Foreground = (Brush)FindResource(
                        string.IsNullOrWhiteSpace(input.Text) ? "TextMuted" : "Danger");
                }
            }

            input.TextChanged += (_, _) => ValidateInput();
            start.Click += (_, _) =>
            {
                ValidateInput();
                if (accepted.Count > 0)
                    dialog.DialogResult = true;
            };
            buttons.Children.Add(cancel);
            buttons.Children.Add(start);
            Grid.SetRow(buttons, 4);
            root.Children.Add(buttons);

            dialog.Content = root;
            input.Focus();
            if (dialog.ShowDialog() != true)
                return false;
            entries = accepted;
            return true;
        }
    }
}
