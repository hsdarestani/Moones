from app.main import app
from app.api.ops_second_real_image_diagnostic import router as ops_second_real_image_router

app.include_router(ops_second_real_image_router)
