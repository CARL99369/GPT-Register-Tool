namespace SmsWorkbench;

internal static class BackendBatchReportDialog
{
    public static Task ShowAsync(
        Window owner,
        BackendBatchReport report,
        string reportPath,
        Action<string> openReport)
    {
        Window dialog = DialogFactory.Create(
            owner,
            report.TaskName + " - 执行报告",
            920,
            620,
            720,
            480,
            ResizeMode.CanResize);
        dialog.ShowInTaskbar = false;

        var root = new Grid { Margin = new Thickness(20) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
        heading.Children.Add(new TextBlock
        {
            Text = report.TaskName,
            FontSize = 20,
            FontWeight = FontWeights.SemiBold,
            Foreground = (Brush)owner.FindResource("TextMain"),
        });
        heading.Children.Add(new TextBlock
        {
            Text = $"{report.Summary}    耗时 {report.DurationText}",
            Margin = new Thickness(0, 6, 0, 0),
            FontSize = 14,
            Foreground = (Brush)owner.FindResource("TextSub"),
        });
        root.Children.Add(heading);

        var grid = new DataGrid
        {
            ItemsSource = report.Items,
            AutoGenerateColumns = false,
            IsReadOnly = true,
            CanUserAddRows = false,
            CanUserDeleteRows = false,
            CanUserReorderColumns = false,
            SelectionMode = DataGridSelectionMode.Single,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
            HeadersVisibility = DataGridHeadersVisibility.Column,
        };
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = "账号",
            Binding = new Binding(nameof(BackendBatchReportItem.Account)),
            Width = new DataGridLength(2, DataGridLengthUnitType.Star),
            MinWidth = 210,
        });
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = "结果",
            Binding = new Binding(nameof(BackendBatchReportItem.Result)),
            Width = 90,
        });
        var reasonStyle = new Style(typeof(TextBlock));
        reasonStyle.Setters.Add(new Setter(TextBlock.TextWrappingProperty, TextWrapping.Wrap));
        reasonStyle.Setters.Add(new Setter(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center));
        reasonStyle.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(4, 6, 4, 6)));
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = "原因 / 状态",
            Binding = new Binding(nameof(BackendBatchReportItem.Reason)),
            Width = new DataGridLength(3, DataGridLengthUnitType.Star),
            MinWidth = 280,
            ElementStyle = reasonStyle,
        });
        Grid.SetRow(grid, 1);
        root.Children.Add(grid);

        var footer = new Grid { Margin = new Thickness(0, 14, 0, 0) };
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        footer.Children.Add(new TextBlock
        {
            Text = reportPath,
            TextTrimming = TextTrimming.CharacterEllipsis,
            VerticalAlignment = VerticalAlignment.Center,
            Foreground = (Brush)owner.FindResource("TextSub"),
        });

        var actions = DialogFactory.CreateActionRow();
        var copyButton = new Button { Content = "复制报告", MinWidth = 90, Margin = new Thickness(8, 0, 0, 0) };
        copyButton.Click += (_, _) => Clipboard.SetText(report.ToClipboardText());
        var openButton = new Button { Content = "打开报告", MinWidth = 90, Margin = new Thickness(8, 0, 0, 0) };
        openButton.Click += (_, _) => openReport?.Invoke(reportPath);
        var closeButton = DialogFactory.CreatePrimaryButton(owner, "关闭", 76);
        closeButton.Margin = new Thickness(8, 0, 0, 0);
        closeButton.Click += (_, _) => dialog.Close();
        actions.Children.Add(copyButton);
        actions.Children.Add(openButton);
        actions.Children.Add(closeButton);
        Grid.SetColumn(actions, 1);
        footer.Children.Add(actions);
        Grid.SetRow(footer, 2);
        root.Children.Add(footer);

        dialog.Content = root;
        var completion = new TaskCompletionSource<bool>();
        dialog.Closed += (_, _) => completion.TrySetResult(true);
        dialog.Show();
        return completion.Task;
    }
}
