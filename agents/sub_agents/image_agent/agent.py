from google.adk.agents.llm_agent import Agent
from agents.tools.image_tools import image_verification
from google.adk.models.lite_llm import LiteLlm



slack_agent_fallback = Agent(
    model=LiteLlm(model="claude-sonnet-4-20250514"),
    name='image_agent',
    description= """Handles all tasks related to images including workout image verification""",

    instruction = """

    As the image agent your responsibility is to handle all image tasks related to Rhett's Fitness Community web app. Your
    main responsibility is image verification, validating user's workout images to confirm their workout. You have the following
    responsibilities:

    1. IMAGE VALIDATION
    Given an image that was provided by my web app, verify that the image aligns with the

    """,
    
    tools=[],
)

