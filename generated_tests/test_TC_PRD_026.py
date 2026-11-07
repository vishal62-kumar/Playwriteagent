import pytest
from playwright.sync_api import expect

def test_TC_PRD_026(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Navigation failed: {e}")
    
    # Step 2: Locate the 'Email' field under BASIC DETAILS section
    email_field = page.locator("[id$='_txtEmail' i]").first
    
    # Step 3: Enter an invalid email format into the 'Email' field
    email_field.fill("invalidemail@")
    
    # Step 4: Attempt to submit the form
    page.locator("[id$='_btnSubmit' i]").first.click()
    
    # Expected: The system should display an error message indicating that the email format is invalid
    expect(page.locator("[id$='_rfvEmail' i]").first).to_be_visible()
