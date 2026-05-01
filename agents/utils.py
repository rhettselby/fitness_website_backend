
import os
from google.adk.runners import Runner
#persistence
from google.adk.sessions import DatabaseSessionService
from .agent import root_agent
from google.genai import types



##### AGENT CALL FUNCTIONS #####

async def process_agent_response(event):
    if event.is_final_response():
        if event.content and event.content.parts:
            return event.content.parts[0].text
    return None



async def call_agent_async(runner, user_id, session_id, query):
    #message sent to agent
    content = types.Content(role="user", parts=[types.Part(text=query)])
    final_response_text = None

    try:
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            response = await process_agent_response(event)

            if response:
                final_response_text = response
        return final_response_text
    
    except Exception as e:
        print(f"Agent Error: {e}")



##### IMPLEMENT PERSISTENCE ######


async def run_agent(query, event_id="DEFAULT"):
    APP_NAME = "rhetts-fitness-community"
    USER_ID = event_id

    db_url = os.environ.get("DATABASE_URL")

    # DatabaseSessionService requires async driver
    if db_url and db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    session_service = DatabaseSessionService(db_url=db_url)
    initial_state = {
    "name": "Rhett",
    }

    existing_sessions = await session_service.list_sessions(
        app_name = APP_NAME,
        user_id = USER_ID,
    )


    #find_existing/create_new session
    if existing_sessions and len(existing_sessions.sessions) > 0:
        SESSION_ID = existing_sessions.sessions[0].id
        print(f"Continuing existing session {SESSION_ID}")

    else:
        new_session = await session_service.create_session(
            app_name = APP_NAME,
            user_id = USER_ID,
            state = initial_state,
        )
        SESSION_ID = new_session.id
    

    #runner
    try:
        runner = Runner(
            agent = root_agent,
            app_name = APP_NAME,
            session_service = session_service
        )

        #call agent with runner + session + query
        return await call_agent_async(runner, USER_ID, SESSION_ID, query)

    except Exception as e:
        print("Model Failed")
        