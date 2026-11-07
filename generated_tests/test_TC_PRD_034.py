import pytest
from playwright.sync_api import expect

def test_TC_PRD_034(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    
    # Step 2: Locate the 'Doctor Type' section on the page
    doctor_type_section = page.locator("[id$='_DoctorTypeSection' i]").first
    
    # Step 3: Select all checkboxes under 'Doctor Type'
    checkboxes = doctor_type_section.locator("input[type='checkbox']")
    for checkbox in checkboxes.all():
        checkbox.check()
    
    # Step 4: Verify that all checkboxes are selected
    for checkbox in checkboxes.all():
        expect(checkbox).to_be_checked()
    
    # Step 5: Deselect all checkboxes under 'Doctor Type'
    for checkbox in checkboxes.all():
        checkbox.uncheck()
    
    # Step 6: Verify that all checkboxes are deselected
    for checkbox in checkboxes.all():
        expect(checkbox).not_to_be_checked()
