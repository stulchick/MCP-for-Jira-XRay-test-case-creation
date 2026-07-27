import json
import re
import requests
import csv
import io
from mcp.server.fastmcp import FastMCP
import os

# ==========================================
# ✏️ Download environment variables from .env file
# ==========================================
from dotenv import load_dotenv
load_dotenv()

# ==========================================
# ✏️ CONFIGURATION & CREDENTIALS
# ==========================================
JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")

JIRA_TOKEN = os.getenv("JIRA_TOKEN")
XRAY_CLIENT_ID = os.getenv("XRAY_CLIENT_ID")
XRAY_CLIENT_SECRET = os.getenv("XRAY_CLIENT_SECRET")


if not all([JIRA_TOKEN, XRAY_CLIENT_ID, XRAY_CLIENT_SECRET]):
    raise ValueError("Missing required environment variables for Jira or Xray!")

# ==========================================
# SESSION SETUP
# ==========================================
jira_session = requests.Session()
jira_session.auth = (JIRA_EMAIL, JIRA_TOKEN)
jira_session.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json"
})

mcp = FastMCP("Xray-QA-Assistant")

def get_xray_cloud_token() -> str:
    """Authenticates with Xray Cloud API v2 and returns a Bearer token."""
    url = "https://xray.cloud.getxray.app/api/v2/authenticate"
    payload = {
        "client_id": XRAY_CLIENT_ID,
        "client_secret": XRAY_CLIENT_SECRET
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return res.text.replace('"', '')
    except requests.RequestException as e:
        error_msg = res.text if 'res' in locals() else str(e)
        raise Exception(f"Xray Cloud Authentication Failed: {error_msg}")

# ==========================================
# CSV EXPORT TOOL
# ==========================================

@mcp.tool()
def export_tests_to_csv(tests_json: str) -> str:
    """
    [SENIOR QA EXPORT TOOL] Converts test cases data into a CSV format compatible with Jira/Xray manual import.
    
    Args:
        tests_json: A JSON string containing a list of tests, e.g.:
        [
            {
                "summary": "Test login with valid credentials",
                "steps": [
                    {"action": "Open login page", "result": "Page is visible"},
                    {"action": "Enter credentials", "result": "Dashboard opens"}
                ]
            }
        ]
    """
    try:
        tests = json.loads(tests_json)
        if not isinstance(tests, list):
            return "❌ Error: tests_json must be a JSON array of test objects."

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        
        # Headers for Xray/Jira standard CSV import
        writer.writerow(["Summary", "Test Type", "Step Action", "Step Data", "Step Expected Result"])

        for test in tests:
            summary = test.get("summary", "Untitled Test")
            steps = test.get("steps", [])
            
            if not steps:
                writer.writerow([summary, "Manual", "", "", ""])
            else:
                for idx, step in enumerate(steps):
                    row_summary = summary if idx == 0 else ""
                    # Handle both AI generation styles ("result" or "expectedResult")
                    exp_result = step.get("expectedResult") or step.get("result", "")
                    
                    writer.writerow([
                        row_summary,
                        "Manual",
                        step.get("action", ""),
                        step.get("data", ""),
                        exp_result
                    ])

        csv_content = output.getvalue()
        return f"✅ CSV generated successfully for manual import:\n\n```csv\n{csv_content}\n```"

    except json.JSONDecodeError as e:
        return f"❌ Failed to parse tests_json: {e}"
    except Exception as e:
        return f"❌ Unexpected error during CSV generation: {str(e)}"
    
# ==========================================
# READ TOOLS (Context & Analysis)
# ==========================================

@mcp.tool()
def get_jira_story(story_key: str) -> str:
    """Fetches summary, description, and acceptance criteria from a Jira User Story."""
    url = f"{JIRA_URL}/rest/api/3/issue/{story_key}"
    try:
        res = jira_session.get(url, timeout=10)
        res.raise_for_status()
        fields = res.json().get('fields', {})
        
        summary = fields.get('summary', 'No summary')
        description = fields.get('description', '')
        if isinstance(description, dict):
            description = json.dumps(description, indent=2)
            
        return f"Story Title: {summary}\nDescription / Acceptance Criteria:\n{description}"
    except requests.RequestException as e:
        return f"Error fetching story {story_key}: {e}"

@mcp.tool()
def get_confluence_page_by_url(url_or_id: str) -> str:
    """Extracts requirements text from a Confluence page URL or ID."""
    page_id_match = re.search(r'/pages/(\d+)', url_or_id)
    page_id = page_id_match.group(1) if page_id_match else (url_or_id if url_or_id.isdigit() else None)
    
    if not page_id:
        return "Failed to extract page ID from URL."

    url = f"{JIRA_URL}/wiki/api/v2/pages/{page_id}?body-format=storage"
    try:
        res = jira_session.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        
        body = data.get('body', {}).get('storage', {}).get('value', '')
        clean_body = re.sub(r'<[^>]+>', ' ', body)
        
        return f"Confluence Title: {data.get('title', '')}\n\nRequirements Content:\n{clean_body.strip()}"
    except requests.RequestException as e:
        return f"Error loading Confluence page {page_id}: {e}"

@mcp.tool()
def get_linked_tests_for_review(story_key: str) -> str:
    """Finds all test cases already linked to a Jira Story using modern JQL search."""
    jql = f"issue in linkedIssues('{story_key}') AND issuetype = 'Test Case'"
    url = f"{JIRA_URL}/rest/api/3/search/jql"
    
    payload = {
        "jql": jql,
        "fields": ["summary"],
        "maxResults": 50
    }
    
    try:
        res = jira_session.post(url, json=payload, timeout=10)
        res.raise_for_status()
        issues = res.json().get('issues', [])
        
        if not issues:
            return f"No Test Cases are currently linked to Story {story_key}."
            
        return "\n".join([f"Test [{issue['key']}]: {issue['fields']['summary']}" for issue in issues])
    except requests.RequestException as e:
        return f"Error executing JQL search: {e} - {res.text if 'res' in locals() else ''}"

@mcp.tool()
def get_issue_link_types() -> str:
    """Returns available Jira issue link types."""
    try:
        res = jira_session.get(f"{JIRA_URL}/rest/api/3/issueLinkType", timeout=10)
        res.raise_for_status()
        types = res.json().get('issueLinkTypes', [])
        lines = [f"Name: '{t['name']}' (Inward: '{t['inward']}', Outward: '{t['outward']}')" for t in types]
        return "Available Issue Link Types:\n" + "\n".join(lines)
    except requests.RequestException as e:
        return f"Error fetching link types: {e}"

@mcp.tool()
def get_test_case_details(test_key: str) -> str:
    """Fetches test case details and step definitions via Xray Cloud GraphQL API for review."""
    try:
        token = get_xray_cloud_token()
    except Exception as err:
        return str(err)

    try:
        issue_res = jira_session.get(f"{JIRA_URL}/rest/api/3/issue/{test_key}?fields=summary,description", timeout=10)
        issue_res.raise_for_status()
        issue_data = issue_res.json()
        issue_id = issue_data.get('id')
        summary = issue_data.get('fields', {}).get('summary', '')
    except requests.RequestException as e:
        return f"Could not find Jira issue {test_key}: {e}"

    graphql_url = "https://xray.cloud.getxray.app/api/v2/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    query = """
    query GetTestSteps($issueId: String!) {
        getTest(issueId: $issueId) {
            steps {
                id
                action
                result
            }
        }
    }
    """

    payload = {
        "query": query,
        "variables": {"issueId": issue_id}
    }

    try:
        res = requests.post(graphql_url, json=payload, headers=headers, timeout=10)
        res_raise = res.json()
        
        if "errors" in res_raise:
            return f"Error fetching test steps: {res_raise['errors']}"

        test_info = res_raise.get('data', {}).get('getTest', {})
        steps = test_info.get('steps', [])

        steps_formatted = []
        for idx, s in enumerate(steps, start=1):
            steps_formatted.append(f"Step {idx}:\n  Action: {s.get('action')}\n  Expected: {s.get('result')}")

        steps_text = "\n".join(steps_formatted) if steps_formatted else "No steps defined yet."

        return f"Test Case: {test_key} - {summary}\n\nSteps:\n{steps_text}"
    except Exception as e:
        return f"Error querying Xray GraphQL: {str(e)}"

# ==========================================
# WRITE TOOLS (Strict Senior QA Gatekeepers)
# ==========================================

@mcp.tool()
def link_existing_test_to_story(test_key: str, story_key: str, link_type_name: str = "Test", confirmed: bool = False) -> str:
    """
    [SENIOR QA RESTRICTED] Links an existing test to a story.
    
    STRICT RULE FOR AI: Never execute without user approval. 
    You MUST output a clear plan (Where, What, Why) first and wait for user confirmation (confirmed=True).
    """
    if not confirmed:
        return f"❌ ACCESS DENIED: You must present a plan to the user and obtain explicit confirmation before linking {test_key} to {story_key}."

    link_data = {
        "type": {"name": link_type_name},
        "inwardIssue": {"key": test_key},
        "outwardIssue": {"key": story_key}
    }
    try:
        res = jira_session.post(f"{JIRA_URL}/rest/api/3/issueLink", json=link_data, timeout=10)
        res.raise_for_status()
        return f"✅ Successfully linked {test_key} to {story_key} using link type '{link_type_name}'."
    except requests.RequestException as e:
        error_payload = res.text if 'res' in locals() else str(e)
        return f"❌ Failed to link {test_key} to {story_key}. Error: {error_payload}"

@mcp.tool()
def post_review_comment(story_key: str, comment_text: str, confirmed: bool = False) -> str:
    """
    [SENIOR QA RESTRICTED] Posts a QA review report comment to a Jira Story.
    
    STRICT RULE FOR AI: Show the review text to the user first and get explicit permission (confirmed=True).
    """
    if not confirmed:
        return f"❌ ACCESS DENIED: Show the review comment to the user and get confirmation before posting to {story_key}."

    url = f"{JIRA_URL}/rest/api/3/issue/{story_key}/comment"
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"text": comment_text, "type": "text"}]
                }
            ]
        }
    }
    try:
        res = jira_session.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return f"Review report successfully saved as a comment on {story_key}!"
    except requests.RequestException as e:
        return f"Error posting comment: {e} - {res.text if 'res' in locals() else ''}"

@mcp.tool()
def add_steps_to_existing_test(test_key: str, steps_json: str, confirmed: bool = False) -> str:
    """
    [SENIOR QA RESTRICTED] Adds steps to an existing Test Case via Xray Cloud GraphQL.
    
    STRICT RULE FOR AI: Never call this without user approval. 
    You MUST display the exact steps payload and rationale to the user first, then wait for 'confirmed=True'.
    """
    if not confirmed:
        return f"❌ ACCESS DENIED: You did not obtain user approval to modify {test_key}. Present the steps plan first."

    try:
        token = get_xray_cloud_token()
    except Exception as err:
        return str(err)

    try:
        issue_res = jira_session.get(f"{JIRA_URL}/rest/api/3/issue/{test_key}?fields=id", timeout=10)
        issue_res.raise_for_status()
        issue_id = issue_res.json().get('id')
    except requests.RequestException as e:
        return f"Could not map Jira key '{test_key}' to internal ID. Error: {e}"

    graphql_url = "https://xray.cloud.getxray.app/api/v2/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        steps = json.loads(steps_json)
        if not isinstance(steps, list):
            return "Invalid JSON: steps_json must be a JSON array."
            
        mutation = """
        mutation AddTestStep($issueId: String!, $step: CreateStepInput!) {
            addTestStep(issueId: $issueId, step: $step) {
                id
            }
        }
        """

        added = 0
        execution_logs = []

        for idx, step in enumerate(steps, start=1):
            # Универсально обрабатываем ключи result или expectedResult
            exp_result = step.get("expectedResult") or step.get("result", "")
            
            variables = {
                "issueId": issue_id,
                "step": {
                    "action": str(step.get("action", "")),
                    "expectedResult": str(exp_result)
                }
            }
            
            payload = {"query": mutation, "variables": variables}
            res = requests.post(graphql_url, json=payload, headers=headers, timeout=10)
            
            try:
                res_data = res.json()
            except ValueError:
                execution_logs.append(f"Step {idx}: Critical API Failure (HTTP {res.status_code}) -> {res.text}")
                continue

            if res.status_code == 200 and "errors" not in res_data:
                added += 1
                step_id = res_data.get('data', {}).get('addTestStep', {}).get('id')
                execution_logs.append(f"Step {idx}: Success (Step ID: {step_id})")
            else:
                execution_logs.append(f"Step {idx}: Failed -> {json.dumps(res_data)}")

        return f"Update Summary for {test_key}:\nAdded {added}/{len(steps)} steps successfully.\n\nLogs:\n" + "\n".join(execution_logs)

    except json.JSONDecodeError as e:
        return f"Failed to parse steps_json: {e}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"

@mcp.tool()
def create_xray_test(project_key: str, story_key: str, summary: str, steps_json: str, confirmed: bool = False) -> str:
    """
    [SENIOR QA RESTRICTED] Creates a new Test Case, links it to a Story, and adds steps.
    
    STRICT RULE FOR AI: Present a full creation plan (summary, steps, target story) to the user 
    and obtain explicit confirmation (confirmed=True) before execution.
    """
    if not confirmed:
        return f"❌ ACCESS DENIED: You must show the test creation plan to the user and get confirmation first."

    log = []
    
    issue_data = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": "Test Case"}
        }
    }
    try:
        res = jira_session.post(f"{JIRA_URL}/rest/api/3/issue", json=issue_data, timeout=10)
        res.raise_for_status()
        new_test_key = res.json().get('key')
        log.append(f"✅ Issue Created: {new_test_key}")
    except requests.RequestException as e:
        err_text = res.text if 'res' in locals() else str(e)
        return f"❌ Failed to create issue: {err_text}"

    link_res_msg = link_existing_test_to_story(new_test_key, story_key, link_type_name="Test", confirmed=True)
    log.append(link_res_msg)

    steps_result = add_steps_to_existing_test(new_test_key, steps_json, confirmed=True)
    log.append(steps_result)

    return "\n".join(log)

if __name__ == "__main__":
    mcp.run()