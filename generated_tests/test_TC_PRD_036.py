import pytest
from playwright.sync_api import expect

def test_TC_PRD_036(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    
    # Step 2: Locate the 'Activity Mode' section on the page
    activity_mode_section = page.locator("[id$='_activityModeSection' i]").first
    
    # Step 3: Select all checkboxes under 'Activity Mode'
    checkboxes = activity_mode_section.locator("input[type='checkbox']")
    for checkbox in checkboxes.all():
        checkbox.check()
    
    # Step 4: Verify that each checkbox is selected
    for checkbox in checkboxes.all():
        expect(checkbox).to_be_checked()
    
    # Step 5: Deselect all checkboxes under 'Activity Mode'
    for checkbox in checkboxes.all():
        checkbox.uncheck()
    
    # Step 6: Verify that each checkbox is deselected
    for checkbox in checkboxes.all():
        expect(checkbox).not_to_be_checked()
