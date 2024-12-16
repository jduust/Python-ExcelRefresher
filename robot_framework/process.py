"""This module contains the main process of the robot."""

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from OpenOrchestrator.database.queues import QueueElement
import os
import win32com.client
from office365.runtime.auth.user_credential import UserCredential
from office365.sharepoint.client_context import ClientContext
import time
import json
import datetime
import locale
from pebble import concurrent

@concurrent.process(timeout=60)  # Timeout after 30 minutes (1800 seconds)
def refresh_excel_file_with_timeout(file_path, orchestrator_connection: OrchestratorConnection):
    refresh_excel_file(file_path, orchestrator_connection)  # Calls your actual function

def process(orchestrator_connection: OrchestratorConnection, queue_element: QueueElement | None = None) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")
    data = json.loads(queue_element.data)
     # Assign each field to a named variable
    sharepoint_site = data.get("SharePointSite")
    folder_path = data.get("FolderPath")
    custom_function = data.get("CustomFunction")

    RobotCredentials = orchestrator_connection.get_credential("Robot365User")
    username = RobotCredentials.username
    password = RobotCredentials.password

        # 1. Create the SharePoint client
    client = sharepoint_client(username, password, sharepoint_site, orchestrator_connection)

    try:
        # 2. Download the file from SharePoint
        local_file_path = download_file_from_sharepoint(client, folder_path, orchestrator_connection)

         # Run refresh_excel_file with timeout handling
        future = refresh_excel_file_with_timeout(local_file_path, orchestrator_connection)

        try:
            future.result()  # Wait for the result
        except Exception as e:
            if "timeout" in str(e).lower():  # Check if the exception indicates a timeout
                orchestrator_connection.log_error("refresh_excel_file exceeded the timeout of 30 minutes.")
                raise RuntimeError("refresh_excel_file did not complete within the allowed time.")
            else:
                orchestrator_connection.log_error(f"An error occurred during refresh_excel_file execution: {e}")
                raise RuntimeError(f"Error in refresh_excel_file: {e}")

        upload_file_to_sharepoint(client, folder_path, local_file_path, custom_function, orchestrator_connection)
    except Exception as e:
        os.remove(local_file_path)
        orchestrator_connection.log_error(str(e))
        raise e

def sharepoint_client(username: str, password: str, sharepoint_site_url: str, orchestrator_connection: OrchestratorConnection) -> ClientContext:
    """
    Creates and returns a SharePoint client context.
    """
    # Authenticate to SharePoint
    ctx = ClientContext(sharepoint_site_url).with_credentials(UserCredential(username, password))

    # Load and verify connection
    web = ctx.web
    ctx.load(web)
    ctx.execute_query()

    orchestrator_connection.log_info(f"Authenticated successfully. Site Title: {web.properties['Title']}")
    return ctx


def download_file_from_sharepoint(client: ClientContext, sharepoint_file_url: str, orchestrator_connection: OrchestratorConnection) -> str:
    """
    Downloads a file from SharePoint and returns the local file path.
    Handles both cases where subfolders exist or only the root folder is used.
    """
    # Extract the root folder, folder path, and file name
    path_parts = sharepoint_file_url.split('/')
    DOCUMENT_LIBRARY = path_parts[0]  # Root folder name (document library)
    FOLDER_PATH = '/'.join(path_parts[1:-1]) if len(path_parts) > 2 else ''  # Subfolders inside root, or empty if none
    file_name = path_parts[-1]  # File name

    # Construct the local folder path inside the Documents folder
    documents_folder = os.path.join(os.path.expanduser("~"), "Documents", FOLDER_PATH) if FOLDER_PATH else os.path.join(os.path.expanduser("~"), "Documents", DOCUMENT_LIBRARY)

    # Ensure the folder exists
    if not os.path.exists(documents_folder):
        os.makedirs(documents_folder)

    # Define the download path inside the folder
    download_path = os.path.join(os.getcwd(), file_name)

    # Download the file from SharePoint
    with open(download_path, "wb") as local_file:
        file = (
            client.web.get_file_by_server_relative_path(sharepoint_file_url)
            .download(local_file)
            .execute_query()
        )
    # Define the maximum wait time (60 seconds) and check interval (1 second)
    wait_time = 60  # 60 seconds
    elapsed_time = 0
    check_interval = 1  # Check every 1 second


    # While loop to check if the file exists at `file_path`
    while not os.path.exists(download_path) and elapsed_time < wait_time:
        time.sleep(check_interval)  # Wait 1 second
        elapsed_time += check_interval

    # After the loop, check if the file still doesn't exist and raise an error
    if not os.path.exists(download_path):
        raise FileNotFoundError(f"File not found at {download_path} after waiting for {wait_time} seconds.")

    orchestrator_connection.log_info(f"[Ok] file has been downloaded into: {download_path}")
    return download_path


def refresh_excel_file(file_path: str, orchestrator_connection: OrchestratorConnection):
    """
    Refreshes an Excel file at the specified file path.
    """

    # Open an Instance of Application
    xlapp = win32com.client.DispatchEx("Excel.Application")

    # Optional, e.g., if you want to debug
    xlapp.Visible = False

    # Open File
    Workbook = xlapp.Workbooks.Open(file_path)

    # Refresh all  
    Workbook.RefreshAll()

    # Wait until Refresh is complete
    xlapp.CalculateUntilAsyncQueriesDone()

    # Save File  
    Workbook.Save()
    Workbook.Close(SaveChanges=True)

    # Quit Instance of Application
    xlapp.Quit()

    # Delete Instance of Application
    del Workbook
    del xlapp

    orchestrator_connection.log_info(f"[Ok] Excel file at {file_path} has been refreshed and saved.")

def upload_file_to_sharepoint(client: ClientContext, sharepoint_file_url: str, local_file_path: str, custom_function, orchestrator_connection: OrchestratorConnection):
    """
    Uploads the specified local file back to SharePoint at the given URL.
    Uses the folder path directly to upload files.
    """
    # Extract the root folder, folder path, and file name
    path_parts = sharepoint_file_url.split('/')
    DOCUMENT_LIBRARY = path_parts[0]  # Root folder name (document library)
    FOLDER_PATH = '/'.join(path_parts[1:-1]) if len(path_parts) > 2 else ''  # Subfolders inside root, or empty if none
    file_name = path_parts[-1]  # File name

    # Construct the server-relative folder path (starting with the document library)
    if FOLDER_PATH:
        folder_path = f"{DOCUMENT_LIBRARY}/{FOLDER_PATH}"
    else:
        folder_path = f"{DOCUMENT_LIBRARY}"

    # Get the folder where the file should be uploaded
    target_folder = client.web.get_folder_by_server_relative_url(folder_path)
    client.load(target_folder)
    client.execute_query()

    # Upload the file to the correct folder in SharePoint
    with open(local_file_path, "rb") as file_content:
        uploaded_file = target_folder.upload_file(file_name, file_content).execute_query()


    orchestrator_connection.log_info(f"[Ok] file has been uploaded to: {uploaded_file.serverRelativeUrl} on SharePoint")

    if custom_function == "MonthlyFolder":
        orchestrator_connection.log_info(f"Custom function: {custom_function}")

        library = client.web.lists.get_by_title("Dokumenter")
        client.load(library).execute_query()

        parent_folder = library.root_folder.folders.get_by_url("Historik")
        client.load(parent_folder).execute_query()
    
        locale.setlocale(locale.LC_TIME, "da_DK")
        current_month = datetime.datetime.now().strftime("%B").capitalize()
        current_year = str(datetime.datetime.now().year)
        year_folder = parent_folder.folders.add(current_year).execute_query()
        month_folder = year_folder.folders.add(current_month).execute_query()

        with open(local_file_path, "rb") as file_content:
            uploaded_file_2 = month_folder.upload_file(f'DKPlan_{current_month}_{current_year}.xlsx', file_content).execute_query()
        orchestrator_connection.log_info(f"[Ok] file has been uploaded to: {uploaded_file_2.serverRelativeUrl} on SharePoint")
            
    os.remove(local_file_path)