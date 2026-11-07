import pytest
from playwright.sync_api import expect

def test_TC_PRD_035(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    
    # Step 2: Locate the 'Activity Status' section on the page
    activity_status_section = page.locator("[id$='_ActivityStatusSection' i]").first
    
    # Step 3: Select all checkboxes under 'Activity Status'
    checkboxes = activity_status_section.locator("input[type='checkbox']")
    for checkbox in checkboxes.element_handles():
        checkbox.check()
    
    # Step 4: Verify that all checkboxes are selected
    for checkbox in checkboxes.element_handles():
        assert checkbox.is_checked()
    
    # Step 5: Deselect all checkboxes under 'Activity Status'
    for checkbox in checkboxes.element_handles():
        checkbox.uncheck()
    
    # Step 6: Verify that all checkboxes are deselected
    for checkbox in checkboxes.element_handles():
        assert not checkbox.is_checked()
