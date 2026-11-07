import pytest
from playwright.sync_api import expect

def test_TC_PRD_030(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Navigation failed: {e}")
    
    # Step 2: Locate the 'Last Name' input field under BASIC DETAILS section
    last_name_input = page.locator("[id$='_txtLastName' i]").first
    
    # Step 3: Enter a string exceeding the maximum allowed length (e.g., 256 characters) into the 'Last Name' field
    long_string = "A" * 256
    last_name_input.fill(long_string)
    
    # Step 4: Attempt to submit the form
    page.locator("[id$='_btnSave' i]").first.click()
    
    # Expected: The system should prevent the submission of the form and display an appropriate error message
    expect(page.locator("[id$='_rfvLastName' i]").first).to_be_visible()
