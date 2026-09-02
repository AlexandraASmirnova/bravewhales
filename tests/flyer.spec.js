import { test, expect } from '@playwright/test';

test.describe('Brave Whales flyer page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('index.html');
  });

  test('has correct title and header content', async ({ page }) => {
    await expect(page).toHaveTitle(/Brave Whales Swimming School/);
    await expect(page.locator('h1')).toHaveText('Brave Whales Swimming School');
    await expect(page.locator('.season')).toHaveText(/2026-2027 Season/i);
    await expect(page.locator('.loc')).toContainText('Juanita Aquatic Center');
    await expect(page.locator('header img')).toBeVisible();
  });

  test('header title renders on a single line', async ({ page }) => {
    const box = await page.locator('h1').boundingBox();
    // A single line of text at this font size is well under 60px tall;
    // wrapping to two lines would roughly double the height.
    expect(box.height).toBeLessThan(60);
  });

  test('GROUP card shows hourly price only, no 30-min row', async ({ page }) => {
    const group = page.locator('.pcard', { hasText: 'GROUP' });
    await expect(group).toContainText('small group lesson');
    await expect(group).toContainText('$80');
    await expect(group).toContainText('/ 1hr');
    await expect(group.locator('.pcard-alt')).toHaveCount(0);
  });

  test('PRIVATE card shows hourly and 30-min pricing with 1:1 coaching label', async ({ page }) => {
    const priv = page.locator('.pcard', { hasText: 'PRIVATE' }).first();
    await expect(priv).toContainText('1:1 coaching');
    await expect(priv).toContainText('$200');
    await expect(priv).toContainText('/ 1hr');
    await expect(priv.locator('.pcard-alt')).toContainText('$120');
    await expect(priv.locator('.pcard-alt')).toContainText('/ 30min');
  });

  test('SEMI-PRIVATE card shows hourly and 30-min pricing', async ({ page }) => {
    const semi = page.locator('.pcard', { hasText: 'SEMI-PRIVATE' });
    await expect(semi).toContainText('2 swimmers');
    await expect(semi).toContainText('$300');
    await expect(semi).toContainText('/ 1hr');
    await expect(semi.locator('.pcard-alt')).toContainText('$160');
    await expect(semi.locator('.pcard-alt')).toContainText('/ 30min');
  });

  test('price and unit text share consistent font styling across cards', async ({ page }) => {
    const mainPriceFont = await page.locator('.pcard-price').first().evaluate(
      (el) => getComputedStyle(el).fontFamily
    );
    const altPriceFont = await page.locator('.alt-price').first().evaluate(
      (el) => getComputedStyle(el).fontFamily
    );
    expect(altPriceFont).toBe(mainPriceFont);

    const unit1hr = await page.locator('.pcard-price .unit').first().evaluate(
      (el) => getComputedStyle(el).fontFamily + '|' + getComputedStyle(el).fontSize
    );
    const unit30min = await page.locator('.pcard-alt .unit').first().evaluate(
      (el) => getComputedStyle(el).fontFamily + '|' + getComputedStyle(el).fontSize
    );
    expect(unit30min).toBe(unit1hr);
  });

  test('contact form has all expected fields', async ({ page }) => {
    await expect(page.locator('#name')).toBeVisible();
    await expect(page.locator('#email')).toBeVisible();
    await expect(page.locator('#phone')).toBeVisible();
    await expect(page.locator('#message')).toBeVisible();
    await expect(page.locator('#message')).toHaveAttribute(
      'placeholder',
      "child's age, swimming level, any questions"
    );
    await expect(page.locator('#submit-btn')).toHaveText('Send message');
  });

  test('required fields block submission when empty', async ({ page }) => {
    let requestMade = false;
    await page.route('https://api.web3forms.com/submit', (route) => {
      requestMade = true;
      route.fulfill({ status: 200, body: JSON.stringify({ success: true }) });
    });
    await page.locator('#submit-btn').click();
    expect(requestMade).toBe(false);
    await expect(page.locator('#form-status')).toBeHidden();
  });

  test('shows success message on successful submission', async ({ page }) => {
    await page.route('https://api.web3forms.com/submit', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    });

    await page.fill('#name', 'Test Parent');
    await page.fill('#email', 'parent@example.com');
    await page.fill('#message', 'Interested in group lessons for my 6 year old.');
    await page.click('#submit-btn');

    const status = page.locator('#form-status');
    await expect(status).toHaveClass(/ok/);
    await expect(status).toHaveText(
      "Thank you! Your message has been sent. We’ll get back to you by email soon."
    );
    await expect(page.locator('#name')).toHaveValue('');
  });

  test('shows error message when submission fails', async ({ page }) => {
    await page.route('https://api.web3forms.com/submit', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: false, message: 'Invalid access key' }),
      });
    });

    await page.fill('#name', 'Test Parent');
    await page.fill('#email', 'parent@example.com');
    await page.fill('#message', 'Question about pricing.');
    await page.click('#submit-btn');

    const status = page.locator('#form-status');
    await expect(status).toHaveClass(/err/);
    await expect(status).toContainText('Something went wrong');
  });

  test('submit button re-enables and resets label after submission', async ({ page }) => {
    await page.route('https://api.web3forms.com/submit', (route) => {
      route.fulfill({ status: 200, body: JSON.stringify({ success: true }) });
    });

    await page.fill('#name', 'Test Parent');
    await page.fill('#email', 'parent@example.com');
    await page.fill('#message', 'Hello');
    await page.click('#submit-btn');

    const btn = page.locator('#submit-btn');
    await expect(btn).toHaveText('Send message');
    await expect(btn).toBeEnabled();
  });

  test('wave background graphic is present', async ({ page }) => {
    const waves = page.locator('.bg-waves svg path');
    await expect(waves).toHaveCount(4);
  });

  test('footer shows school name', async ({ page }) => {
    await expect(page.locator('footer')).toContainText('Brave Whales Swimming School');
  });
});
