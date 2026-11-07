import pytest
from playwright.sync_api import expect

def test_TC_PRD_029(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Navigation failed: {e}")
    
    # Step 2: Locate the 'First Name' field in the 'BASIC DETAILS' section
    first_name_field = page.locator("[id$='_txtFirstName' i]").first
    
    # Step 3: Enter a string exceeding the maximum allowed length for the 'First Name' field
    long_string = "A" * 256  # Assuming the max length is 255 characters
    first_name_field.fill(long_string)
    
    # Step 4: Attempt to submit the form
    page.locator("[id$='_btnSave' i]").first.click()
    
    # Expected: The system should prevent submission and display an error message
    expect(page.locator("[id$='_rfvFirstName' i]").first).to_be_visible()
