from app.main import app
from app.api.ops_image_job_diagnostic import router as ops_image_job_diagnostic_router

app.include_router(ops_image_job_diagnostic_router)
