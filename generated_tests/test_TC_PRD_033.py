import pytest
import re
from playwright.sync_api import expect

def test_TC_PRD_033(page):
    # Step 1: Navigate to the Practice Referral Doctor page
    try:
        page.goto("http://localhost:2786/Contacts/PracticeReferralDoctor.aspx")
    except Exception as e:
        pytest.fail(f"Failed to navigate to the page: {e}")

    # Step 2: Locate the 'Country' field and check if it is pre-filled with a valid country name
    country_field = page.locator("[id$='_txtCountry' i]").first
    expect(country_field).to_have_value(re.compile(r'\w+'))

    # Step 3: Locate the 'State' field and check if it is pre-filled with a valid state name
    state_field = page.locator("[id$='_txtState' i]").first
    expect(state_field).to_have_value(re.compile(r'\w+'))

    # Step 4: Locate the 'City' field and check if it is pre-filled with a valid city name
    city_field = page.locator("[id$='_txtCity' i]").first
    expect(city_field).to_have_value(re.compile(r'\w+'))

    # Step 5: Verify the visibility of the element with id ending in 'VIEWSTATE'
    viewstate_element = page.locator("[id$='VIEWSTATE' i]").first
    expect(viewstate_element).to_be_hidden()

    # Expected: The 'Country', 'State', and 'City' fields should be pre-filled with valid data
    # and the 'VIEWSTATE' element should be hidden.
