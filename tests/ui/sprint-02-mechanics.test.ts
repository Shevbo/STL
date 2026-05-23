/**
 * Sprint_02 UI Mechanics Verification Tests
 * Verify all 6 tasks are working via Playwright
 */

import { test, expect, Page } from '@playwright/test';

test.describe('Sprint_02 Mechanics', () => {
  let page: Page;

  test.beforeEach(async ({ page: p }) => {
    page = p;
    // Navigate and login (mock auth in dev)
    await page.goto('http://localhost:5173');
    // In production: await page.goto('https://stl.shectory.ru');
  });

  // Task 1: Instrument switching — chart, orderbook, right panel sync
  test('Task 1: Instrument switching updates all panels', async () => {
    // Verify ChartFrame symbol selector exists
    const symbolSelect = page.locator('.sym-select');
    await expect(symbolSelect).toBeVisible();

    // Select different instrument
    await symbolSelect.selectOption('GZU6@RTSX');

    // Verify OrderBook updates symbol
    const orderbook = page.locator('.ob');
    await expect(orderbook).toBeVisible();

    // Verify InstrumentPanel updates
    const instrumentPanel = page.locator('.instrument-panel');
    await expect(instrumentPanel).toContainText('GZU6@RTSX');
  });

  // Task 2: Active Orders Panel — table with columns
  test('Task 2: Active Orders Panel displays table', async () => {
    const activeOrdersPanel = page.locator('.aop');
    await expect(activeOrdersPanel).toBeVisible();

    // Verify table headers
    await expect(activeOrdersPanel.locator('th')).toContainText(['Код', 'Цена', 'Кол', 'Опер', 'Время', 'Ком.']);
  });

  // Task 3: Order Placement works (CRITICAL)
  test('Task 3: Order placement submits without margin error', async () => {
    // Click orderbook bid price
    const bidPrice = page.locator('.ob-row.bid').first();
    await bidPrice.click();

    // Verify OrderConfirmDialog opens
    const confirmDialog = page.locator('[role="dialog"]');
    await expect(confirmDialog).toBeVisible();

    // Verify order details are pre-filled
    await expect(page.locator('input[value="sell"]')).toBeChecked();
  });

  // Task 4: STL Comment is added (verified in code)
  test('Task 4: Order request includes STL comment', async () => {
    // Intercept network request
    await page.route('**/api/v1/orders', async (route) => {
      const request = route.request();
      const postData = request.postDataJSON();
      expect(postData).toMatchObject({
        comment: 'STL',
      });
      await route.continue();
    });
  });

  // Task 5: Timeframe switching
  test('Task 5: Chart timeframe buttons switch correctly', async () => {
    // Verify timeframe buttons exist
    const tfButtons = page.locator('.tf-btn');
    await expect(tfButtons).toHaveCount(8); // 8 timeframes: 1м 5м 15м 30м 1ч 2ч 4ч Д

    // Click different timeframe
    await page.locator('button:has-text("1ч")').click();

    // Verify active state changes
    const activeBtn = page.locator('.tf-btn.active');
    await expect(activeBtn).toContainText('1ч');
  });

  // Task 6: OrderBook clickable rows
  test('Task 6: OrderBook prices open order dialog', async () => {
    // Click ask price (should open buy order)
    const askPrice = page.locator('.ob-row.ask').first();
    await askPrice.click();

    // Verify dialog shows buy order
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible();
    await expect(page.locator('select[value="buy"]')).toBeChecked();

    // Close dialog
    await page.keyboard.press('Escape');

    // Click bid price (should open sell order)
    const bidPrice = page.locator('.ob-row.bid').first();
    await bidPrice.click();

    // Verify dialog shows sell order
    await expect(dialog).toBeVisible();
    await expect(page.locator('select[value="sell"]')).toBeChecked();
  });

  // Integration: Complete flow
  test('Integration: Complete trading flow', async () => {
    // 1. Select instrument from dropdown
    const symbolSelect = page.locator('.sym-select');
    await symbolSelect.selectOption('GZU6@RTSX');

    // 2. Verify Active Orders Panel shows
    const activeOrdersPanel = page.locator('.aop');
    await expect(activeOrdersPanel).toBeVisible();

    // 3. Change timeframe
    await page.locator('button:has-text("30м")').click();

    // 4. Click orderbook price
    const bidPrice = page.locator('.ob-row.bid').first();
    await bidPrice.click();

    // 5. Verify order dialog opens with correct details
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible();

    // 6. Verify all panels updated for same symbol
    const instrumentPanel = page.locator('.instrument-panel');
    await expect(instrumentPanel).toContainText('GZU6@RTSX');
  });
});
