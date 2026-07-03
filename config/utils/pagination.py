from rest_framework.pagination import PageNumberPagination
from config.utils.response import SuccessResponse


class StandardResultsSetPagination(PageNumberPagination):
    """
    Custom pagination class that extends DRF's PageNumberPagination
    and integrates with custom response format.
    """
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return SuccessResponse(
            data={
                "count": self.page.paginator.count,
                "total_pages": self.page.paginator.num_pages,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )
