
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
from agents.sub_agents import image_agent


root_agent = Agent(
    model=LiteLlm(model="claude-sonnet-4-20250514"),
    name='root_agent_fallback',
    description= "Fallback agent that routes incoming tasks to the appropriate sub-agent",

    instruction = 
    """
    You are the root agent of my fitness web-app. Your job is to delegate the queries you recieve to the appropriate sub-agent 
    to ensurethat the given task is completed successfuly. 

    It is extremely important that you select the sub_agent that aligns most accurately with the given task. You have the
    following sub agents:

    1. Image agent - responsible for handling any image related tasks include workout image validation
    """,

    tools=[],
    sub_agents=[image_agent]
)






