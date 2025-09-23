class LibTemplate < Formula
  include Language::Python::Virtualenv

  desc "Rich-powered logging helpers for colorful terminal output"
  homepage "https://github.com/bitranox/lib_log_rich"
  url "https://github.com/bitranox/lib_log_rich/archive/refs/tags/v1.4.0.tar.gz"
  sha256 "<fill-in-release-sha256>"
  license "MIT"

  depends_on "python@3.10"

  resource "rich" do
    url "https://files.pythonhosted.org/packages/fe/75/af448d8e52bf1d8fa6a9d089ca6c07ff4453d86c65c145d0a300bb073b9b/rich-14.1.0.tar.gz"
    sha256 "e497a48b844b0320d45007cdebfeaeed8db2a4f4bcf49f15e455cfc4af11eaa8"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    system("python3", "-c", "import lib_log_rich as m; print(m.summary_info())")
  end
end
