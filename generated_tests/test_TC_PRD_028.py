import pytest
from playwright.sync_api import expect

def test_TC_PRD_028(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    
    # Step 2: Locate the 'ZIP / Postal Code' field under the 'CLINIC & ADDRESS' section
    zip_code_field = page.locator("[id$='_txtZipCode' i]").first  # Use .first to ensure single element selection
    
    # Step 3: Attempt to enter alphabetic characters into the 'ZIP / Postal Code' field
    zip_code_field.fill("ABCDE")
    
    # Step 4: Try to submit the form or move to the next field
    page.locator("[id$='_btnSave' i]").first.click()  # Use .first to ensure single element selection
    
    # Expected: The 'ZIP / Postal Code' field should not accept alphabetic characters and should display an error message or prevent form submission
    expect(page.locator("[id$='_rfvZipCode' i]").first).to_be_visible()  # Use .first to ensure single element selection and visibility check
