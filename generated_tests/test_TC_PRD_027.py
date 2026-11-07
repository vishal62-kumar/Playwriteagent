import pytest
from playwright.sync_api import expect

def test_TC_PRD_027(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Navigation failed: {e}")
    
    # Step 2: Locate the 'Mobile Number' input field
    mobile_number_field = page.locator("[id$='_txtMobileNumber' i]").first
    
    # Step 3: Attempt to enter alphabetic characters into the 'Mobile Number' field
    mobile_number_field.fill("abcde")
    
    # Step 4: Try to submit the form or move to the next field
    page.locator("[id$='_btnSave' i]").first.click()
    
    # Expected: The 'Mobile Number' field should not accept alphabetic characters and should display an error message or prevent submission
    expect(page.locator("[id$='_rfvMobileNumber' i]").first).to_be_visible()
