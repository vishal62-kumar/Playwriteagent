import pytest
from playwright.sync_api import expect

def test_TC_PRD_031(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    
    # Step 2: Locate the 'Password' field on the page
    password_field = page.locator("[id$='_txtPassword' i]").first
    
    # Step 3: Enter a sample password into the 'Password' field
    sample_password = "SamplePassword123"
    password_field.fill(sample_password)
    
    # Step 4: Check if the input type is 'password' to ensure characters are masked
    expect(password_field).to_have_attribute("type", "password")
    
    # Additional Step: Ensure the page title or a specific element is visible to confirm navigation
    expect(page.get_by_text("Add Referral Doctor").first).to_be_visible()
