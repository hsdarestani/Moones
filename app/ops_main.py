from app.main import app
from app.api.ops_image_acceptance_diagnostic import router as ops_image_acceptance_router

app.include_router(ops_image_acceptance_router)
