import pytest
from playwright.sync_api import expect

def test_TC_PRD_032(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Navigation failed: {e}")

    # Step 2: Locate the 'Change Password' checkbox on the page
    change_password_checkbox = page.locator("[id$='_chkChangePassword' i]").first
    
    # Step 3: Select the 'Change Password' checkbox
    change_password_checkbox.check()
    
    # Expected: The new password field should be enabled and allow input
    new_password_field = page.locator("[id$='_txtNewPassword' i]").first
    expect(new_password_field).to_be_enabled()
