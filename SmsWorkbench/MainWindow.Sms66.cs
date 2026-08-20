using System.Text.Json.Nodes;

namespace SmsWorkbench
{
    public partial class MainWindow
    {
        private async Task<bool> ShowSms66OneClickDialogAsync()
        {
            string apiKey = ResolveSms66ApiKey(settingsService.GetString("phone_reuse.sms66.api_key"));
            if (apiKey.Length == 0)
            {
                ShowThemedInfoDialog("SMS66 未配置", "请先在设置的手机接码分类中填写 SMS66 API Key。");
                return false;
            }

            string endpoint = FirstNonEmpty(settingsService.GetString("phone_reuse.sms66.endpoint"), Sms66CatalogClient.DefaultEndpoint);
            string projectId = FirstNonEmpty(settingsService.GetString("phone_reuse.sms66.project_id"), Sms66CatalogClient.OpenAiProjectId);
            IReadOnlyList<Sms66PhoneChoice> available;
            try
            {
                System.Windows.Input.Mouse.OverrideCursor = System.Windows.Input.Cursors.Wait;
                available = await Sms66CatalogClient.LoadAvailableNumbersAsync(
                    httpClient, apiKey, endpoint, projectId);
            }
            catch (Exception exc)
            {
                logger?.Error(exc, "Failed to load SMS66 designated numbers");
                if (Sms66CatalogClient.IsDesignatedPurchaseUnavailable(exc))
                {
                    return ContinueWithRandomSms66Purchase(
                        projectId,
                        "项目 " + projectId + " 不支持指定号码库存，已切换为普通随机购买。");
                }
                ShowThemedInfoDialog("SMS66 加载失败", "无法读取项目 " + projectId + " 的可购号码：" + exc.Message);
                return false;
            }
            finally
            {
                System.Windows.Input.Mouse.OverrideCursor = null;
            }

            if (available.Count == 0)
            {
                return ContinueWithRandomSms66Purchase(
                    projectId,
                    "项目 " + projectId + " 当前没有指定库存，已切换为普通随机购买。");
            }

            string savedPrefix = DigitsOnly(settingsService.GetString("phone_reuse.sms66.phone_prefix"));
            string savedPhone = NormalizeSms66Phone(settingsService.GetString("phone_reuse.sms66.designated_phone"));
            var dialog = new Window
            {
                Title = "SMS66 选号",
                Owner = this,
                Width = Math.Min(680, SystemParameters.WorkArea.Width - 60),
                Height = 410,
                MinWidth = 560,
                MinHeight = 380,
                ResizeMode = ResizeMode.CanResize,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Background = (Brush)FindResource("AppBg")
            };

            var root = new Grid { Margin = new Thickness(24) };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 18) };
            heading.Children.Add(new TextBlock
            {
                Text = "选择 SMS66 号码",
                FontSize = 20,
                FontWeight = FontWeights.SemiBold,
                Foreground = (Brush)FindResource("TextMain"),
                Margin = new Thickness(0, 0, 0, 4)
            });
            heading.Children.Add(new TextBlock
            {
                Text = $"项目 {projectId} · 可购 {available.Count} 个",
                FontSize = 13,
                Foreground = (Brush)FindResource("TextSub")
            });
            Grid.SetRow(heading, 0);
            root.Children.Add(heading);

            var prefixPanel = CreateSmsBowerDialogRow("号段前缀", out ContentControl prefixHost);
            var prefixBox = new TextBox
            {
                Text = savedPrefix,
                MinHeight = 36,
                Padding = new Thickness(10, 6, 10, 6),
                ToolTip = "例如 1202；留空显示全部可购号码"
            };
            prefixHost.Content = prefixBox;
            Grid.SetRow(prefixPanel, 1);
            root.Children.Add(prefixPanel);

            var phonePanel = CreateSmsBowerDialogRow("可购号码", out ContentControl phoneHost);
            var phoneBox = new ComboBox
            {
                DisplayMemberPath = nameof(Sms66PhoneChoice.DisplayName),
                IsTextSearchEnabled = true,
                MaxDropDownHeight = 280,
                MinHeight = 36,
                Padding = new Thickness(8, 4, 8, 4)
            };
            phoneHost.Content = phoneBox;
            Grid.SetRow(phonePanel, 2);
            root.Children.Add(phonePanel);

            var status = new TextBlock
            {
                Foreground = (Brush)FindResource("TextMuted"),
                FontSize = 12,
                Margin = new Thickness(142, 8, 0, 0),
                TextWrapping = TextWrapping.Wrap
            };
            Grid.SetRow(status, 3);
            root.Children.Add(status);

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
                IsCancel = true
            };
            var start = new Button
            {
                Content = "购买并接码",
                MinWidth = 112,
                Height = 36,
                IsDefault = true
            };
            start.Click += (_, _) => dialog.DialogResult = true;
            buttons.Children.Add(cancel);
            buttons.Children.Add(start);
            Grid.SetRow(buttons, 4);
            root.Children.Add(buttons);

            void RefreshChoices()
            {
                string prefix = DigitsOnly(prefixBox.Text);
                var filtered = available
                    .Where(item => DigitsOnly(item.Phone).StartsWith(prefix, StringComparison.Ordinal))
                    .ToList();
                phoneBox.ItemsSource = filtered;
                phoneBox.SelectedItem = filtered.FirstOrDefault(item => item.Phone == savedPhone) ?? filtered.FirstOrDefault();
                start.IsEnabled = filtered.Count > 0;
                status.Text = filtered.Count == 0
                    ? "该号段当前没有可购号码，请缩短前缀或清空后重试。"
                    : $"匹配 {filtered.Count} 个；选择号码后点击“购买并接码”才会扣费。";
            }

            prefixBox.TextChanged += (_, _) => RefreshChoices();
            RefreshChoices();
            dialog.Content = root;
            if (dialog.ShowDialog() != true || phoneBox.SelectedItem is not Sms66PhoneChoice chosen)
                return false;

            settingsService.UpdateConfig(root =>
            {
                JsonObject phoneReuse = GetOrCreateSection(root, "phone_reuse");
                JsonObject sms66 = GetOrCreateSection(phoneReuse, "sms66");
                phoneReuse["source"] = "sms66";
                sms66["project_id"] = projectId;
                sms66["phone_prefix"] = DigitsOnly(prefixBox.Text);
                sms66["designated_phone"] = chosen.Phone;
            });
            return true;
        }

        private bool ContinueWithRandomSms66Purchase(
            string projectId,
            string message)
        {
            try
            {
                settingsService.UpdateConfig(root =>
                {
                    JsonObject phoneReuse = GetOrCreateSection(root, "phone_reuse");
                    JsonObject sms66 = GetOrCreateSection(phoneReuse, "sms66");
                    phoneReuse["source"] = "sms66";
                    sms66["project_id"] = projectId;
                    sms66["phone_prefix"] = "";
                    sms66["designated_phone"] = "";
                });
            }
            catch (Exception exc)
            {
                logger?.Error(exc, "Failed to save SMS66 random purchase fallback");
                ShowThemedInfoDialog("SMS66 配置保存失败", exc.Message);
                return false;
            }

            ShowThemedInfoDialog("SMS66 已切换", message + "后续由项目接口随机分配号码。 ");
            return true;
        }

        private static string ResolveSms66ApiKey(string configured)
        {
            string value = (configured ?? "").Trim();
            if (value.StartsWith('$'))
                return (Environment.GetEnvironmentVariable(value[1..]) ?? "").Trim();
            return value;
        }

        private static string DigitsOnly(string value) => new((value ?? "").Where(char.IsDigit).ToArray());

        private static string NormalizeSms66Phone(string value)
        {
            string digits = DigitsOnly(value);
            return digits.Length == 0 ? "" : "+" + digits;
        }
    }
}
