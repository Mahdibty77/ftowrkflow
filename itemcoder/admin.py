from django.contrib import admin

from .models import CodeTable, CodeTableRow, PriceList, CodePrice, ConfigDocument


@admin.register(CodeTable)
class CodeTableAdmin(admin.ModelAdmin):
    list_display = ("group", "row_count", "updated_at")
    search_fields = ("group",)


@admin.register(CodeTableRow)
class CodeTableRowAdmin(admin.ModelAdmin):
    list_display = ("group", "row_no")
    list_filter = ("group",)
    search_fields = ("group",)


@admin.register(PriceList)
class PriceListAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "is_active", "created_at")
    search_fields = ("name",)


@admin.register(CodePrice)
class CodePriceAdmin(admin.ModelAdmin):
    list_display = ("code", "price_list", "price", "updated_at")
    list_filter = ("price_list",)
    search_fields = ("code",)
    list_editable = ("price",)


@admin.register(ConfigDocument)
class ConfigDocumentAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "is_active", "created_at")
    list_filter = ("key", "is_active")
    search_fields = ("key",)
